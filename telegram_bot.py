#!/usr/bin/env python3
"""
بوت تلجرام لإنشاء إيميلات مؤقتة متعددة عبر mail.tm.

الميزات:
- واجهة عربية فقط.
- لوحة مشرف وإدارة أعضاء.
- اشتراك إجباري وحظر وإذاعة.
- رسالة ترحيب مدمجة بالقائمة الرئيسية.
- حد قابل للتعديل لعدد الإيميلات لكل مستخدم.
- عرض دومينات مدفوعة شكلية يديرها الأدمن.
- رسائل واضحة لأسباب فشل تحميل صندوق الوارد.
- أزرار تلجرام ملوّنة.
"""

import asyncio
import json
import os
import re
import secrets
import string
import time
import traceback
from datetime import timedelta
from html import escape, unescape

import psycopg2
import requests
from psycopg2.extras import Json, RealDictCursor
from telegram import InlineKeyboardButton as TelegramInlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ================== إعدادات عامة ==================
API = "https://api.mail.tm"
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "6436207302"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

# توجيه رسائل المستخدمين للأدمن
forwarding_enabled = False

# حالة تشغيل البوت (الأدمن دائماً يستطيع الدخول)
bot_active = True
bot_offline_message = ""
bot_offline_message_html = ""

# 0 يعني غير محدود إلى أن يحدد الأدمن رقماً من لوحة التحكم
DEFAULT_EMAIL_LIMIT = 0
MEMBERS_PAGE_SIZE = 10
BOT_STARTED_AT = time.time()
CREATE_EMAIL_COOLDOWN_SECONDS = 5
INBOX_COOLDOWN_SECONDS = 3
LEGACY_MAIL_PASSWORD = os.getenv("MAIL_TM_LEGACY_PASSWORD", "TempMail123")
FIXED_MAIL_PASSWORD = "TempMail123"
USER_ACTION_TIMESTAMPS = {}
DEFAULT_SUBSCRIPTION_MESSAGE = (
    "⚠️ يجب عليك الاشتراك في القنوات التالية لاستخدام البوت:\n\n"
    "بعد الاشتراك في جميع القنوات اضغط: ✅ التحقق من الاشتراك"
)


def consume_action_cooldown(user_id: int, action: str, cooldown_seconds: int) -> int:
    """يرجع الثواني المتبقية، أو صفر ويسجل العملية إذا كان مسموحاً تنفيذها."""
    now = time.monotonic()
    key = (int(user_id), str(action))
    last_time = USER_ACTION_TIMESTAMPS.get(key)
    if last_time is not None:
        remaining = cooldown_seconds - (now - last_time)
        if remaining > 0:
            return max(1, int(remaining) + 1)

    USER_ACTION_TIMESTAMPS[key] = now
    if len(USER_ACTION_TIMESTAMPS) > 5000:
        cutoff = now - 3600
        stale_keys = [item for item, stamp in USER_ACTION_TIMESTAMPS.items() if stamp < cutoff]
        for stale_key in stale_keys:
            USER_ACTION_TIMESTAMPS.pop(stale_key, None)
    return 0


def _default_button_style(text: str, callback_data: str | None = None, url: str | None = None) -> str:
    """اختيار لون افتراضي مرتب لكل زر دون تغيير وظيفته."""
    value = f"{text or ''} {callback_data or ''}".lower()
    danger_words = (
        "حذف", "إلغاء", "حظر", "إيقاف", "تعطيل", "danger", "delete", "remove", "ban", "cancel",
    )
    success_words = (
        "إنشاء", "تأكيد", "تحقق", "تفعيل", "تشغيل", "تحديث", "حفظ", "إضافة", "فك حظر",
        "success", "create", "confirm", "verify", "enable", "refresh", "save", "add", "unban",
    )
    if any(word in value for word in danger_words):
        return "danger"
    if any(word in value for word in success_words):
        return "success"
    if url:
        return "primary"
    return "primary"


def InlineKeyboardButton(text, *args, style=None, transparent=False, **kwargs):
    """غلاف يلوّن كل الأزرار افتراضياً، مع السماح بزر شفاف مقصود فقط."""
    if transparent:
        return TelegramInlineKeyboardButton(text, *args, **kwargs)
    if style is None:
        style = _default_button_style(
            str(text),
            callback_data=kwargs.get("callback_data"),
            url=kwargs.get("url"),
        )
    return TelegramInlineKeyboardButton(text, *args, style=style, **kwargs)

# ================== قاعدة البيانات ==================

def get_db_connection():
    try:
        if not DATABASE_URL:
            print("❌ DATABASE_URL غير موجود")
            return None
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ خطأ في الاتصال بقاعدة البيانات: {e}")
        return None


def init_database():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            # users
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_users (
                    telegram_id BIGINT PRIMARY KEY,
                    language VARCHAR(10),
                    first_name VARCHAR(255),
                    last_name VARCHAR(255),
                    username VARCHAR(255),
                    emails JSONB DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # channels
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id SERIAL PRIMARY KEY,
                    channel_username VARCHAR(255) UNIQUE NOT NULL,
                    channel_id BIGINT,
                    channel_title VARCHAR(500),
                    subscription_message TEXT,
                    subscription_enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # admins
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # settings (جديد)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # banned users (جديد)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    telegram_id BIGINT PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    banned_by BIGINT
                )
            """)

            # email seen state (جديد) - لتتبع آخر رسالة تم إرسالها لكل بريد
            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_seen (
                    email_address TEXT PRIMARY KEY,
                    last_message_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # يمنع تكرار إشعار اشتراك العضو في القناة الإجبارية.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscription_notifications (
                    telegram_id BIGINT NOT NULL,
                    channel_key TEXT NOT NULL,
                    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (telegram_id, channel_key)
                )
            """)

            # إحصائيات الاستخدام اليومية.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage_daily_stats (
                    stat_date DATE PRIMARY KEY DEFAULT CURRENT_DATE,
                    new_users BIGINT NOT NULL DEFAULT 0,
                    emails_created BIGINT NOT NULL DEFAULT 0,
                    inbox_opens BIGINT NOT NULL DEFAULT 0
                )
            """)

            # الواجهة أصبحت عربية فقط؛ توحيد بيانات المستخدمين القديمة دون تغيير بنية الجدول.
            cur.execute("UPDATE bot_users SET language='ar' WHERE language IS DISTINCT FROM 'ar'")

            conn.commit()
            print("✅ تم تهيئة قاعدة البيانات بنجاح")
    except Exception as e:
        print(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        conn.rollback()
    finally:
        conn.close()


def load_user_data():
    conn = get_db_connection()
    if not conn:
        return {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT telegram_id, language, first_name, last_name, username, emails FROM bot_users")
            rows = cur.fetchall()

        user_data = {}
        for row in rows:
            user_id = str(row["telegram_id"])
            user_data[user_id] = {
                "lang": "ar",
                "first_name": row.get("first_name", "") or "",
                "last_name": row.get("last_name", "") or "",
                "username": row.get("username", "") or "",
                "emails": row.get("emails") or [],
            }
        return user_data
    except Exception as e:
        print(f"❌ خطأ في تحميل البيانات: {e}")
        return {}
    finally:
        conn.close()


def save_single_user(telegram_id, user_info) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_users (telegram_id, language, first_name, last_name, username, emails, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    language = EXCLUDED.language,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    username = EXCLUDED.username,
                    emails = EXCLUDED.emails,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                int(telegram_id),
                user_info.get("lang"),
                user_info.get("first_name", ""),
                user_info.get("last_name", ""),
                user_info.get("username", ""),
                Json(user_info.get("emails", [])),
            ))
            conn.commit()
            return True
    except Exception as e:
        print(f"❌ خطأ في حفظ البيانات: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


# ---------- Settings (جديد) ----------
def get_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    if not conn:
        return default
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else default
    except Exception as e:
        print(f"⚠️ خطأ في get_setting: {e}")
        return default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_settings(key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(key)
                DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP
            """, (key, value))
            conn.commit()
            return True
    except Exception as e:
        print(f"⚠️ خطأ في set_setting: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()



def message_custom_emoji_html(message) -> str:
    """تحويل نص رسالة تلجرام إلى HTML آمن مع إبقاء Custom Emoji في مكانه."""
    text = str(getattr(message, "text", None) or "")
    entities = [
        entity
        for entity in (getattr(message, "entities", None) or ())
        if getattr(entity, "type", "") == "custom_emoji"
        and getattr(entity, "custom_emoji_id", None)
    ]
    if not entities:
        return escape(text, quote=False)

    encoded = text.encode("utf-16-le")
    parts = []
    cursor = 0
    for entity in sorted(entities, key=lambda item: int(item.offset)):
        start = max(0, int(entity.offset))
        end = max(start, start + int(entity.length))
        if start < cursor:
            continue
        before = encoded[cursor * 2:start * 2].decode("utf-16-le")
        emoji_text = encoded[start * 2:end * 2].decode("utf-16-le")
        parts.append(escape(before, quote=False))
        emoji_id = escape(str(entity.custom_emoji_id), quote=True)
        parts.append(
            f'<tg-emoji emoji-id="{emoji_id}">{escape(emoji_text, quote=False)}</tg-emoji>'
        )
        cursor = end

    parts.append(encoded[cursor * 2:].decode("utf-16-le"))
    # آخر جزء لم يمر عبر escape حتى الآن.
    parts[-1] = escape(parts[-1], quote=False)
    return "".join(parts)


def save_rich_text_setting(key: str, message) -> bool:
    """حفظ النص العادي ونسخته التي تحتوي معرفات الإيموجي المميز."""
    text = str(getattr(message, "text", None) or "")
    rich_html = message_custom_emoji_html(message)
    if not set_setting(key, text):
        return False
    if not set_setting(f"{key}_rich_html", rich_html):
        # منع بقاء نسخة Rich قديمة لا تطابق النص الجديد.
        set_setting(f"{key}_rich_html", "")
        return False
    return True


def get_rich_text_setting(key: str, default: str = ""):
    """إرجاع النص العادي وHTML الآمن؛ متوافق مع الإعدادات القديمة."""
    text = get_setting(key, default)
    if not str(text or "").strip() and default:
        text = default
        return text, escape(text, quote=False)

    rich_html = get_setting(f"{key}_rich_html", "")
    if rich_html:
        return text, rich_html
    return text, escape(str(text or ""), quote=False)

def increment_daily_stat(stat_name: str) -> bool:
    """زيادة عداد يومي واحد بشكل ذري وآمن مع تعدد المستخدمين."""
    allowed = {"new_users", "emails_created", "inbox_opens"}
    if stat_name not in allowed:
        return False

    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO usage_daily_stats(stat_date, {stat_name})
                VALUES (CURRENT_DATE, 1)
                ON CONFLICT(stat_date)
                DO UPDATE SET {stat_name} = usage_daily_stats.{stat_name} + 1
                """
            )
            conn.commit()
            return True
    except Exception as error:
        print(f"⚠️ خطأ في تسجيل إحصائية {stat_name}: {error}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_last_seven_days_usage():
    """إرجاع آخر 7 أيام بما فيها الأيام التي لم يحدث فيها استخدام."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT CURRENT_DATE AS today")
            today = cur.fetchone()["today"]
            cur.execute("""
                SELECT stat_date, new_users, emails_created, inbox_opens
                FROM usage_daily_stats
                WHERE stat_date BETWEEN CURRENT_DATE - INTERVAL '6 days' AND CURRENT_DATE
                ORDER BY stat_date DESC
            """)
            rows = cur.fetchall()

        by_date = {row["stat_date"]: row for row in rows}
        result = []
        for offset in range(7):
            day = today - timedelta(days=offset)
            row = by_date.get(day) or {}
            result.append({
                "stat_date": day,
                "new_users": int(row.get("new_users") or 0),
                "emails_created": int(row.get("emails_created") or 0),
                "inbox_opens": int(row.get("inbox_opens") or 0),
            })
        return result
    except Exception as error:
        print(f"⚠️ خطأ في قراءة الإحصائيات اليومية: {error}")
        return []
    finally:
        conn.close()


def get_global_subscription_message() -> str:
    """رسالة اشتراك إجبارية عامة واحدة لكل القنوات."""
    value = get_setting("global_subscription_message", DEFAULT_SUBSCRIPTION_MESSAGE)
    return value if str(value or "").strip() else DEFAULT_SUBSCRIPTION_MESSAGE


def get_global_subscription_message_html() -> str:
    value, rich_html = get_rich_text_setting(
        "global_subscription_message",
        DEFAULT_SUBSCRIPTION_MESSAGE,
    )
    if not str(value or "").strip():
        return escape(DEFAULT_SUBSCRIPTION_MESSAGE, quote=False)
    return rich_html


def set_global_subscription_message(message: str) -> bool:
    value = str(message or "")
    if not value.strip():
        return False
    if not set_setting("global_subscription_message", value):
        return False
    return set_setting("global_subscription_message_rich_html", "")


def get_email_limit() -> int:
    """يرجع الحد العام لكل مستخدم؛ صفر يعني غير محدود."""
    raw_value = get_setting("email_limit", str(DEFAULT_EMAIL_LIMIT)).strip()
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return DEFAULT_EMAIL_LIMIT


def get_member_email_limit(user_id: int):
    """يرجع الحد الخاص بعضو معيّن، أو None ليستخدم الحد العام."""
    raw_value = get_setting(f"member_email_limit_{int(user_id)}", "").strip()
    if raw_value == "":
        return None
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return None


def set_member_email_limit(user_id: int, limit: int) -> bool:
    """حفظ حد خاص للعضو؛ صفر يعني غير محدود لهذا العضو."""
    return set_setting(f"member_email_limit_{int(user_id)}", str(max(0, int(limit))))


def get_effective_email_limit(user_id: int) -> int:
    """الحد الخاص يسبق الحد العام عند وجوده."""
    member_limit = get_member_email_limit(user_id)
    return member_limit if member_limit is not None else get_email_limit()


def check_database_health():
    """فحص اتصال قاعدة البيانات مع زمن الاستجابة بالمللي ثانية."""
    started = time.perf_counter()
    conn = get_db_connection()
    if not conn:
        return False, None, "تعذر الاتصال بقاعدة البيانات"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
        elapsed = int((time.perf_counter() - started) * 1000)
        return bool(row and row[0] == 1), elapsed, ""
    except Exception as error:
        elapsed = int((time.perf_counter() - started) * 1000)
        return False, elapsed, str(error)[:120]
    finally:
        conn.close()


def check_mail_service_health():
    """فحص خدمة mail.tm مع زمن الاستجابة بالمللي ثانية."""
    started = time.perf_counter()
    response, request_error = mail_request("GET", "/domains", return_error=True)
    elapsed = int((time.perf_counter() - started) * 1000)
    if response is not None and response.status_code == 200:
        try:
            domains = response.json().get("hydra:member") or []
            if isinstance(domains, list):
                return True, elapsed, ""
        except (ValueError, AttributeError):
            return False, elapsed, "استجابة غير صالحة"
    if response is not None:
        return False, elapsed, f"HTTP {response.status_code}"
    return False, elapsed, request_error or "تعذر الاتصال"


def format_bot_uptime() -> str:
    total_seconds = max(0, int(time.time() - BOT_STARTED_AT))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours or days:
        parts.append(f"{hours} ساعة")
    if minutes or hours or days:
        parts.append(f"{minutes} دقيقة")
    if not parts:
        parts.append(f"{seconds} ثانية")
    return " و".join(parts)


def normalize_telegram_username(value: str) -> str:
    """تنظيف والتحقق من يوزر تلجرام دون @."""
    username = str(value or "").strip().lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return username
    return ""


def get_admin_contact_username() -> str:
    return normalize_telegram_username(get_setting("admin_contact_username", ""))


def normalize_paid_domain(value: str) -> str:
    """تنظيف اسم دومين شكلي مدفوع والتحقق من صيغته."""
    domain = str(value or "").strip().lower().lstrip("@")
    if len(domain) > 253 or "." not in domain:
        return ""

    labels = domain.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        return ""
    return domain


def get_paid_domains() -> list[str]:
    """جلب الدومينات الشكلية المدفوعة من الإعدادات بترتيب إضافتها."""
    raw = get_setting("paid_domains", "")
    domains = []
    for line in raw.splitlines():
        domain = normalize_paid_domain(line)
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def save_paid_domains(domains) -> bool:
    cleaned = []
    for value in domains:
        domain = normalize_paid_domain(value)
        if domain and domain not in cleaned:
            cleaned.append(domain)
    return set_setting("paid_domains", "\n".join(cleaned))


def add_paid_domain(value: str):
    domain = normalize_paid_domain(value)
    if not domain:
        return False, "invalid", ""

    domains = get_paid_domains()
    if domain in domains:
        return False, "exists", domain

    domains.append(domain)
    if not save_paid_domains(domains):
        return False, "save_failed", domain
    return True, "added", domain


def remove_paid_domain(index: int):
    domains = get_paid_domains()
    if index < 0 or index >= len(domains):
        return False, ""

    removed = domains.pop(index)
    if not save_paid_domains(domains):
        return False, ""
    return True, removed


def build_main_menu_text(user_id: int) -> str:
    """دمج رسالة الترحيب مع القائمة الرئيسية في رسالة واحدة."""
    emails_count = len(get_user_emails(user_id))
    menu_text = get_text("ar", "main_menu", emails_count=emails_count)
    welcome_message = get_setting("welcome_message", "")
    if str(welcome_message or "").strip():
        return f"{welcome_message}\n\n{menu_text}"
    return menu_text


def build_main_menu_html(user_id: int) -> str:
    """نسخة HTML من القائمة الرئيسية تحافظ على الإيموجي المميز برسالة الترحيب."""
    emails_count = len(get_user_emails(user_id))
    menu_text = get_text("ar", "main_menu", emails_count=emails_count)
    welcome_text, welcome_html = get_rich_text_setting("welcome_message", "")
    if str(welcome_text or "").strip():
        return f"{welcome_html}\n\n{escape(menu_text, quote=False)}"
    return escape(menu_text, quote=False)


def telegram_html(value) -> str:
    """حماية النصوص الخارجية قبل إرسالها بوضع HTML في تلجرام."""
    return escape(str(value or ""), quote=False)


# ---------- Ban (جديد) ----------
def is_banned(user_id: int) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM banned_users WHERE telegram_id=%s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        print(f"⚠️ خطأ في is_banned: {e}")
        return False
    finally:
        conn.close()


def ban_user_db(user_id: int, reason: str, banned_by: int) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO banned_users(telegram_id, reason, banned_by)
                VALUES (%s, %s, %s)
                ON CONFLICT(telegram_id)
                DO UPDATE SET reason=EXCLUDED.reason, banned_by=EXCLUDED.banned_by, banned_at=CURRENT_TIMESTAMP
            """, (user_id, reason, banned_by))
            conn.commit()
            return True
    except Exception as e:
        print(f"⚠️ خطأ في ban_user_db: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def unban_user_db(user_id: int) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM banned_users WHERE telegram_id=%s", (user_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"⚠️ خطأ في unban_user_db: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()




# ---------- إشعارات الاشتراك الإجباري ----------
def subscription_notification_exists(user_id: int, channel_key: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM subscription_notifications WHERE telegram_id=%s AND channel_key=%s",
                (user_id, channel_key),
            )
            return cur.fetchone() is not None
    except Exception as e:
        print(f"⚠️ خطأ في فحص سجل إشعار الاشتراك: {e}")
        return False
    finally:
        conn.close()


def mark_subscription_notified(user_id: int, channel_key: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO subscription_notifications(telegram_id, channel_key)
                VALUES (%s, %s)
                ON CONFLICT (telegram_id, channel_key) DO NOTHING
                """,
                (user_id, channel_key),
            )
            conn.commit()
            return True
    except Exception as e:
        print(f"⚠️ خطأ في حفظ سجل إشعار الاشتراك: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


# ---------- Email Seen (جديد) ----------
def get_last_seen_message_id(email_address: str) -> str:
    """يرجع آخر message_id تم إرساله لهذا البريد (أو نص فارغ)."""
    conn = get_db_connection()
    if not conn:
        return ""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_message_id FROM email_seen WHERE email_address=%s", (email_address.lower(),))
            row = cur.fetchone()
            return row[0] if row and row[0] else ""
    except Exception as e:
        print(f"⚠️ خطأ في get_last_seen_message_id: {e}")
        return ""
    finally:
        conn.close()


def set_last_seen_message_id(email_address: str, message_id: str) -> None:
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO email_seen(email_address, last_message_id, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(email_address)
                DO UPDATE SET last_message_id=EXCLUDED.last_message_id, updated_at=CURRENT_TIMESTAMP
            """, (email_address.lower(), message_id))
            conn.commit()
    except Exception as e:
        print(f"⚠️ خطأ في set_last_seen_message_id: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
def delete_email_seen_records(addresses) -> None:
    clean_addresses = [str(address).lower() for address in addresses if address]
    if not clean_addresses:
        return

    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM email_seen WHERE email_address = ANY(%s)", (clean_addresses,))
            conn.commit()
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف سجل الرسائل: {e}")
        conn.rollback()
    finally:
        conn.close()


def _db_row_to_user_info(row) -> dict:
    """تحويل صف العضو من PostgreSQL إلى نفس شكل بيانات الذاكرة."""
    return {
        "lang": "ar",
        "first_name": row.get("first_name", "") or "",
        "last_name": row.get("last_name", "") or "",
        "username": row.get("username", "") or "",
        "emails": row.get("emails") or [],
    }


def find_user_by_username_or_id(search_value: str):
    """البحث عن العضو من PostgreSQL مباشرة، ثم مزامنة نسخة الذاكرة."""
    query = str(search_value or "").strip().lstrip("@").lower()
    if not query:
        return None

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if query.isdigit():
                    cur.execute(
                        """
                        SELECT telegram_id, language, first_name, last_name, username, emails
                        FROM bot_users
                        WHERE telegram_id=%s
                        LIMIT 1
                        """,
                        (int(query),),
                    )
                else:
                    cur.execute(
                        """
                        SELECT telegram_id, language, first_name, last_name, username, emails
                        FROM bot_users
                        WHERE LOWER(COALESCE(username, ''))=%s
                        LIMIT 1
                        """,
                        (query,),
                    )
                row = cur.fetchone()

            if row:
                user_id = int(row["telegram_id"])
                info = _db_row_to_user_info(row)
                user_database[str(user_id)] = info
                return user_id, info
        except Exception as error:
            print(f"⚠️ فشل البحث عن العضو في قاعدة البيانات: {error}")
        finally:
            conn.close()

    # احتياط عند تعذر الاتصال المؤقت بقاعدة البيانات.
    if query.isdigit() and query in user_database:
        return int(query), user_database[query]

    for uid, info in user_database.items():
        username = str((info or {}).get("username") or "").lower()
        if username and username == query:
            return int(uid), info
    return None


def clear_user_emails(user_id: int):
    """حذف إيميلات العضو من PostgreSQL مباشرة وتحديث الذاكرة فوراً."""
    conn = get_db_connection()
    if not conn:
        return False, 0

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT telegram_id, language, first_name, last_name, username, emails
                FROM bot_users
                WHERE telegram_id=%s
                FOR UPDATE
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False, 0

            old_emails = list(row.get("emails") or [])
            addresses = [(item or {}).get("address") for item in old_emails]
            cur.execute(
                """
                UPDATE bot_users
                SET emails='[]'::jsonb, updated_at=CURRENT_TIMESTAMP
                WHERE telegram_id=%s
                """,
                (int(user_id),),
            )
            conn.commit()

        info = _db_row_to_user_info(row)
        info["emails"] = []
        user_database[str(user_id)] = info
        delete_email_seen_records(addresses)
        return True, len(old_emails)
    except Exception as error:
        print(f"❌ فشل حذف إيميلات العضو من قاعدة البيانات: {error}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False, 0
    finally:
        conn.close()


# ================== إدارة المشرفين (مثل كودك) ==================

def get_all_admins():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM admins ORDER BY created_at DESC")
            return cur.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب المشرفين: {e}")
        return []
    finally:
        conn.close()


def is_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admins WHERE telegram_id=%s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        print(f"❌ خطأ في التحقق من المشرف: {e}")
        return False
    finally:
        conn.close()


def add_admin(telegram_id, username=None, first_name=None, added_by=None):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO admins (telegram_id, username, first_name, added_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (telegram_id, username, first_name, added_by))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"❌ خطأ في إضافة المشرف: {e}")
        return False
    finally:
        conn.close()


def remove_admin(telegram_id):
    if telegram_id == ADMIN_ID:
        return False
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM admins WHERE telegram_id=%s", (telegram_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"❌ خطأ في إزالة المشرف: {e}")
        return False
    finally:
        conn.close()


# ================== إدارة القنوات (اشتراك إجباري متعدد) ==================

def get_channels(only_enabled=True):
    """جلب كل قنوات الاشتراك، مع الحفاظ على ترتيب إضافتها."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if only_enabled:
                cur.execute("""
                    SELECT id, channel_username, channel_id, channel_title,
                           subscription_message, subscription_enabled, created_at
                    FROM channels
                    WHERE subscription_enabled = TRUE
                    ORDER BY created_at ASC, id ASC
                """)
            else:
                cur.execute("""
                    SELECT id, channel_username, channel_id, channel_title,
                           subscription_message, subscription_enabled, created_at
                    FROM channels
                    ORDER BY created_at ASC, id ASC
                """)
            return cur.fetchall()
    except Exception as e:
        print(f"❌ خطأ في الحصول على قائمة القنوات: {e}")
        return []
    finally:
        conn.close()


def get_channel_by_id(channel_db_id: int):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, channel_username, channel_id, channel_title,
                       subscription_message, subscription_enabled, created_at
                FROM channels
                WHERE id=%s
                LIMIT 1
            """, (int(channel_db_id),))
            return cur.fetchone()
    except Exception as e:
        print(f"❌ خطأ في جلب القناة: {e}")
        return None
    finally:
        conn.close()


def get_channel_info(only_enabled=True):
    """للتوافق مع الأجزاء القديمة: يرجع قناة واحدة فقط عند الحاجة."""
    channels = get_channels(only_enabled=only_enabled)
    if not channels:
        return None
    return channels[0] if only_enabled else channels[-1]


def set_channel(channel_username, channel_id=None, channel_title=None):
    """إضافة قناة جديدة أو تحديث القناة نفسها بدون حذف القنوات الأخرى."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channels (channel_username, channel_id, channel_title, subscription_enabled)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (channel_username)
                DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    channel_title = EXCLUDED.channel_title,
                    subscription_enabled = TRUE,
                    updated_at = CURRENT_TIMESTAMP
            """, (channel_username, channel_id, channel_title))
            conn.commit()
            return True
    except Exception as e:
        print(f"❌ خطأ في إضافة القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def set_channel_message(channel_username, message):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM channels WHERE channel_username=%s", (channel_username,))
            if not cur.fetchone():
                return False
            cur.execute("""
                UPDATE channels
                SET subscription_message=%s, updated_at=CURRENT_TIMESTAMP
                WHERE channel_username=%s
            """, (message, channel_username))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"❌ خطأ في تعيين رسالة القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_channel(channel_username):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM channels WHERE channel_username=%s", (channel_username,))
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    except Exception as e:
        print(f"❌ خطأ في حذف القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def toggle_subscription(channel_username):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channels
                SET subscription_enabled = NOT subscription_enabled, updated_at=CURRENT_TIMESTAMP
                WHERE channel_username=%s
                RETURNING subscription_enabled
            """, (channel_username,))
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else False
    except Exception as e:
        print(f"❌ خطأ في تبديل حالة الاشتراك: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_channel_subscription_stats():
    """إحصائيات التحقق المسجلة لكل قناة اشتراك إجباري."""
    channels = get_channels(only_enabled=False)
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT channel_key, COUNT(*)
                FROM subscription_notifications
                GROUP BY channel_key
            """)
            counts = {str(row[0]).lower(): int(row[1] or 0) for row in cur.fetchall()}

        result = []
        for channel in channels:
            item = dict(channel)
            channel_key = str(
                item.get("channel_id") or item.get("channel_username") or ""
            ).lower()
            item["verified_count"] = counts.get(channel_key, 0)
            result.append(item)
        return result
    except Exception as error:
        print(f"⚠️ خطأ في إحصائيات قنوات الاشتراك: {error}")
        return None
    finally:
        conn.close()


# ================== اشتراك إجباري متعدد ==================

async def get_missing_subscription_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """يرجع القنوات المفعّلة التي لم يشترك بها العضو بعد."""
    missing = []
    channels = get_channels(only_enabled=True)

    for channel_info in channels:
        channel_username = channel_info["channel_username"]
        channel_id = channel_info.get("channel_id")
        chat_identifier = channel_id if channel_id else f"@{channel_username}"
        subscribed = False
        temporary_failure = False

        for attempt in range(2):
            try:
                member = await context.bot.get_chat_member(chat_identifier, user_id)
                subscribed = member.status in ("member", "administrator", "creator")
                break
            except Exception as error:
                error_text = str(error).lower()
                temporary_failure = any(term in error_text for term in (
                    "readerror", "timeout", "timed out", "network", "connection",
                    "bad gateway", "temporarily unavailable", "server error",
                ))
                if temporary_failure and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue

                print(
                    f"⚠️ فشل فحص اشتراك المستخدم {user_id} في @{channel_username}: "
                    f"{type(error).__name__}: {error}"
                )
                break

        # عطل الشبكة المؤقت لا يمنع المستخدم، مثل السلوك السابق.
        if temporary_failure and not subscribed:
            continue
        if not subscribed:
            missing.append(channel_info)

    return missing


async def check_user_subscription_strict(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يجب أن يكون العضو مشتركاً بكل القنوات المفعّلة."""
    missing = await get_missing_subscription_channels(user_id, context)
    return len(missing) == 0


def subscription_prompt(_lang: str, channels, message: str = ""):
    """عرض الرسالة العامة مع القنوات الناقصة وأزرار الانضمام."""
    if isinstance(channels, str):
        channels = [{
            "channel_username": channels,
            "channel_title": channels,
        }]
    channels = list(channels or [])

    channel_lines = []
    for index, channel in enumerate(channels, start=1):
        username = str(channel.get("channel_username") or "").lstrip("@")
        title = str(channel.get("channel_title") or username)
        channel_lines.append(
            f"{index}. 📢 {telegram_html(title)} — @{telegram_html(username)}"
        )

    channels_text = "\n".join(channel_lines)
    global_message = get_global_subscription_message_html()
    message_parts = global_message.split("\n\n", 1)
    if len(message_parts) == 2:
        text = f"{message_parts[0]}\n\n{channels_text}\n\n{message_parts[1]}"
    elif channels_text:
        text = f"{global_message}\n\n{channels_text}"
    else:
        text = global_message

    rows = []
    for channel in channels:
        username = str(channel.get("channel_username") or "").lstrip("@")
        title = str(channel.get("channel_title") or username)
        display_title = title if len(title) <= 28 else title[:25] + "..."
        rows.append([
            InlineKeyboardButton(
                f"📢 الانضمام: {display_title}",
                url=f"https://t.me/{username}",
                style="primary",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "✅ التحقق من الاشتراك",
            callback_data="verify_subscription",
            style="success",
        )
    ])
    return text, InlineKeyboardMarkup(rows)

# ================== mail.tm API ==================
# ================== mail.tm API ==================

def mail_request(method: str, path: str, return_error=False, **kwargs):
    """طلب إلى mail.tm مع إعادة محاولة وإبقاء سبب الفشل عند طلبه."""
    last_response = None
    last_error_code = None
    supplied_headers = kwargs.pop("headers", {}) or {}
    request_headers = {"User-Agent": "TelegramTempMailBot/3.1", **supplied_headers}

    for attempt in range(3):
        try:
            response = requests.request(
                method,
                f"{API}{path}",
                timeout=(5, 20),
                headers=request_headers,
                **kwargs,
            )
            last_response = response
            if response.status_code not in (429, 500, 502, 503, 504):
                return (response, None) if return_error else response

            last_error_code = f"http_{response.status_code}"
            print(f"⚠️ mail.tm {path}: HTTP {response.status_code}")
        except requests.Timeout as error:
            last_error_code = "timeout"
            print(f"⚠️ mail.tm {path}: {type(error).__name__}: {error}")
        except requests.ConnectionError as error:
            last_error_code = "connection"
            print(f"⚠️ mail.tm {path}: {type(error).__name__}: {error}")
        except requests.RequestException as error:
            last_error_code = "network"
            print(f"⚠️ mail.tm {path}: {type(error).__name__}: {error}")

        if attempt < 2:
            time.sleep(2 ** attempt)

    if return_error:
        return last_response, last_error_code or "network"
    return last_response


def get_available_domains():
    response = mail_request("GET", "/domains")
    if response is None or response.status_code != 200:
        return []
    try:
        data = response.json()
    except ValueError as error:
        print(f"⚠️ رد النطاقات غير صالح: {error}")
        return []

    domains = data.get("hydra:member") or []
    available = []
    for item in domains:
        domain = item.get("domain")
        if not domain:
            continue
        if item.get("isActive") is False or item.get("isPrivate") is True:
            continue
        if domain not in available:
            available.append(domain)
    return available


def create_email():
    """إنشاء بريد مع تجربة الدومينات المجانية المتاحة تلقائياً عند فشل أحدها."""
    try:
        domains = get_available_domains()
        if not domains:
            return None, None, None

        domains = list(domains)
        secrets.SystemRandom().shuffle(domains)
        username_chars = string.ascii_lowercase + string.digits

        for domain in domains:
            for _ in range(2):
                username = "".join(secrets.choice(username_chars) for _ in range(10))
                email_address = f"{username}@{domain}"
                password = FIXED_MAIL_PASSWORD

                response = mail_request(
                    "POST",
                    "/accounts",
                    json={"address": email_address, "password": password},
                )
                if response is None:
                    print(f"⚠️ تعذر إنشاء حساب على الدومين @{domain}، تجربة دومين آخر")
                    break

                if response.status_code == 422:
                    continue

                if response.status_code != 201:
                    print(
                        f"⚠️ فشل إنشاء حساب على @{domain}: HTTP {response.status_code}، "
                        "تجربة دومين آخر"
                    )
                    break

                token_response = mail_request(
                    "POST",
                    "/token",
                    json={"address": email_address, "password": password},
                )
                if token_response is None or token_response.status_code != 200:
                    status = token_response.status_code if token_response is not None else "network"
                    print(f"⚠️ فشل جلب توكن البريد {email_address}: {status}")
                    break

                try:
                    token = token_response.json().get("token")
                except (ValueError, AttributeError):
                    token = None
                if token:
                    return email_address, token, password
                break

        return None, None, None
    except Exception as error:
        print(f"❌ create_email: {type(error).__name__}: {error}")
        return None, None, None


def create_email_with_domain(domain):
    """إنشاء بريد على دومين مجاني محدد من قائمة mail.tm المتاحة حالياً."""
    try:
        domain = str(domain or "").strip().lower().lstrip("@")
        if not domain:
            return None, None, None

        available_domains = get_available_domains()
        if domain not in available_domains:
            print(f"⚠️ الدومين @{domain} لم يعد متاحاً ضمن الدومينات المجانية العامة")
            return None, None, None

        username_chars = string.ascii_lowercase + string.digits

        for _ in range(2):
            username = "".join(secrets.choice(username_chars) for _ in range(10))
            email_address = f"{username}@{domain}"
            password = FIXED_MAIL_PASSWORD

            response = mail_request(
                "POST",
                "/accounts",
                json={"address": email_address, "password": password},
            )
            if response is None:
                break
            if response.status_code == 422:
                continue
            if response.status_code != 201:
                print(f"⚠️ فشل إنشاء حساب على @{domain}: HTTP {response.status_code}")
                break

            token_response = mail_request(
                "POST",
                "/token",
                json={"address": email_address, "password": password},
            )
            if token_response is None or token_response.status_code != 200:
                status = token_response.status_code if token_response is not None else "network"
                print(f"⚠️ فشل جلب توكن البريد {email_address}: {status}")
                break

            try:
                token = token_response.json().get("token")
            except (ValueError, AttributeError):
                token = None
            if token:
                return email_address, token, password
            break

        return None, None, None
    except Exception as error:
        print(f"❌ create_email_with_domain: {type(error).__name__}: {error}")
        return None, None, None


def refresh_email_token_data(email_data):
    """تجديد توكن بريد واحد من العنوان وكلمة المرور المحفوظة."""
    if not isinstance(email_data, dict):
        return None

    address = str(email_data.get("address") or "").strip()
    password = str(email_data.get("password") or LEGACY_MAIL_PASSWORD)
    if not address or not password:
        return None

    response = mail_request(
        "POST",
        "/token",
        json={"address": address, "password": password},
    )
    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "network"
        print(f"⚠️ تعذر تجديد توكن {address}: {status}")
        return None

    try:
        token = response.json().get("token")
    except (ValueError, AttributeError):
        token = None
    if not token:
        return None

    email_data["token"] = token
    email_data["password"] = password
    return token


def refresh_user_email_token(user_id: int, email_index: int):
    """تجديد توكن بريد المستخدم وحفظه في PostgreSQL."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return None

    token = refresh_email_token_data(emails[email_index])
    if not token:
        return None

    data = get_user_data(user_id)
    user_database[str(user_id)] = data
    if not save_single_user(str(user_id), data):
        print(f"⚠️ تم تجديد التوكن لكن تعذر حفظه للمستخدم {user_id}")
    return token


def check_user_inbox_detailed(user_id: int, email_index: int):
    """فحص الصندوق وتجديد التوكن تلقائياً مرة واحدة عند HTTP 401."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return {"messages": None, "error": "email_missing", "status": None}

    email_data = emails[email_index]
    result = check_inbox_detailed(email_data.get("token"))
    if result.get("error") != "token_invalid":
        return result

    new_token = refresh_user_email_token(user_id, email_index)
    if not new_token:
        return result

    retry_result = check_inbox_detailed(new_token)
    retry_result["token_refreshed"] = retry_result.get("error") is None
    return retry_result


def get_user_message_content(user_id: int, email_index: int, message_id: str):
    """تحميل رسالة كاملة، مع تجديد التوكن تلقائياً إذا انتهى أثناء الفتح."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return None

    token = emails[email_index].get("token")
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {token}"}
        response = mail_request("GET", f"/messages/{message_id}", headers=headers)
        if response is not None and response.status_code == 200:
            try:
                return response.json()
            except ValueError as error:
                print(f"⚠️ رد محتوى الرسالة غير صالح: {error}")
                return None

        if attempt == 0 and response is not None and response.status_code == 401:
            token = refresh_user_email_token(user_id, email_index)
            if token:
                continue
        return None
    return None


def check_inbox_detailed(token):
    """فحص الصندوق مع سبب واضح للفشل دون كشف التوكن."""
    headers = {"Authorization": f"Bearer {token}"}
    response, request_error = mail_request(
        "GET",
        "/messages",
        headers=headers,
        return_error=True,
    )

    if response is None:
        return {"messages": None, "error": request_error or "network", "status": None}

    status = response.status_code
    if status == 200:
        try:
            payload = response.json()
            messages = payload.get("hydra:member", [])
            if not isinstance(messages, list):
                raise ValueError("hydra:member ليس قائمة")
            return {"messages": messages, "error": None, "status": 200}
        except (ValueError, AttributeError) as error:
            print(f"⚠️ رد صندوق الوارد غير صالح: {error}")
            return {"messages": None, "error": "invalid_response", "status": 200}

    if status == 401:
        print("⚠️ توكن البريد غير صالح أو منتهي")
        error_code = "token_invalid"
    elif status == 429:
        print("⚠️ ضغط مؤقت على خدمة البريد: HTTP 429")
        error_code = "rate_limited"
    elif status in (500, 502, 503, 504):
        print(f"⚠️ خدمة البريد غير متاحة مؤقتاً: HTTP {status}")
        error_code = "service_unavailable"
    else:
        print(f"⚠️ فشل فحص الصندوق: HTTP {status}")
        error_code = "http_error"

    return {"messages": None, "error": error_code, "status": status}


def check_inbox(token):
    """واجهة متوافقة مع منطق عرض تفاصيل الرسالة القديم."""
    result = check_inbox_detailed(token)
    return result["messages"] if result.get("error") is None else None


def build_inbox_error_view(error_code, email_index: int, status=None):
    """رسالة وأزرار مناسبة لسبب فشل تحميل صندوق الوارد."""
    if error_code == "token_invalid":
        text = (
            "⚠️ تعذر تجديد جلسة هذا البريد تلقائياً.\n\n"
            "قد تكون بيانات البريد قديمة أو لم يعد الحساب متاحاً على Mail.tm."
        )
        rows = [
            [InlineKeyboardButton(
                "🗑️ حذف هذا البريد",
                callback_data=f"confirm_delete_{email_index}",
                style="danger",
            )],
            [InlineKeyboardButton(
                "✨ إنشاء بريد جديد",
                callback_data="create_email",
                style="success",
            )],
            [InlineKeyboardButton(
                get_text("ar", "btn_back"),
                callback_data="select_inbox",
                style="primary",
            )],
        ]
        return text, InlineKeyboardMarkup(rows)

    if error_code == "rate_limited":
        text = (
            "⏳ يوجد ضغط مؤقت على خدمة البريد.\n\n"
            "انتظر قليلاً ثم اضغط تحديث للمحاولة من جديد."
        )
    elif error_code == "service_unavailable":
        text = (
            "🛠️ خدمة البريد متوقفة أو تواجه مشكلة مؤقتة.\n\n"
            "حاول التحديث بعد قليل."
        )
    elif error_code == "timeout":
        text = (
            "⏱️ انتهت مهلة الاتصال بخدمة البريد.\n\n"
            "هذه مشكلة مؤقتة، حاول التحديث لاحقاً."
        )
    elif error_code in ("connection", "network"):
        text = (
            "🌐 تعذر الاتصال بخدمة البريد مؤقتاً.\n\n"
            "تحقق لاحقاً واضغط تحديث للمحاولة."
        )
    elif error_code == "invalid_response":
        text = (
            "⚠️ وصلت استجابة غير صالحة من خدمة البريد.\n\n"
            "حاول التحديث بعد قليل."
        )
    elif error_code == "http_error" and status:
        text = (
            f"⚠️ تعذر تحميل الرسائل بسبب خطأ HTTP {status}.\n\n"
            "حاول التحديث لاحقاً."
        )
    else:
        text = get_text("ar", "error_load_messages")

    rows = [
        [InlineKeyboardButton(
            get_text("ar", "btn_refresh"),
            callback_data=f"inbox_{email_index}",
            style="success",
        )],
        [InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data="select_inbox",
            style="primary",
        )],
    ]
    return text, InlineKeyboardMarkup(rows)


def get_message_content(message_id, token):
    headers = {"Authorization": f"Bearer {token}"}
    response = mail_request("GET", f"/messages/{message_id}", headers=headers)
    if response is None or response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError as error:
        print(f"⚠️ رد محتوى الرسالة غير صالح: {error}")
        return None


def extract_otp(text):
    if not text:
        return None
    match = re.search(r"\b(\d{4,8})\b", str(text))
    return match.group(1) if match else None


def normalize_text_value(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    return str(value or "")


def html_to_text(html_value) -> str:
    """تحويل HTML إلى نص آمن للعرض داخل تلجرام."""
    html_text = normalize_text_value(html_value)
    if not html_text:
        return ""

    # فك الترميز أولاً حتى تُحذف الوسوم التي وصلت بصورة &lt;tag&gt;.
    html_text = unescape(html_text)
    html_text = re.sub(
        r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>",
        " ",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_text = re.sub(r"<\s*br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    html_text = re.sub(r"</\s*p\s*>", "\n", html_text, flags=re.IGNORECASE)
    html_text = re.sub(r"<[^>]+>", " ", html_text)
    html_text = re.sub(r"[\t\r ]+", " ", html_text)
    html_text = re.sub(r"\n{3,}", "\n\n", html_text)
    return html_text.strip()


def get_message_text(full: dict) -> str:
    """يرجع أفضل نص متاح من رسالة mail.tm."""
    if not full:
        return ""

    text_value = normalize_text_value(full.get("text")).strip()
    if text_value:
        return text_value

    intro = normalize_text_value(full.get("intro")).strip()
    if intro:
        return intro

    html_value = full.get("html")
    return html_to_text(html_value) if html_value else ""

# ================== بيانات المستخدمين ==================

init_database()
forwarding_enabled = get_setting("forwarding_enabled", "0") == "1"
user_database = load_user_data()


def get_user_data(user_id):
    uid = str(user_id)
    if uid not in user_database:
        user_database[uid] = {"lang": "ar", "emails": []}
        save_single_user(uid, user_database[uid])
    else:
        user_database[uid]["lang"] = "ar"
    return user_database[uid]


def get_user_emails(user_id):
    return get_user_data(user_id).get("emails", [])


def get_user_language(_user_id):
    return "ar"


def update_user_info(user_id, user):
    if user is None:
        return
    data = get_user_data(user_id)
    data["lang"] = "ar"
    data["first_name"] = user.first_name or ""
    data["last_name"] = user.last_name or ""
    data["username"] = user.username or ""
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)


def set_user_language(user_id, _lang="ar", user=None):
    """للتوافق مع البيانات القديمة؛ اللغة ثابتة دائماً على العربية."""
    data = get_user_data(user_id)
    data["lang"] = "ar"
    if user:
        data["first_name"] = user.first_name or ""
        data["last_name"] = user.last_name or ""
        data["username"] = user.username or ""
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)


def add_user_email(user_id, email, token, password=None):
    data = get_user_data(user_id)
    email_record = {"address": email, "token": token}
    if password:
        email_record["password"] = password
    data.setdefault("emails", []).append(email_record)
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)


def remove_user_email(user_id, email):
    data = get_user_data(user_id)
    data["emails"] = [item for item in data.get("emails", []) if item.get("address") != email]
    user_database[str(user_id)] = data
    if save_single_user(str(user_id), data):
        delete_email_seen_records([email])

async def notify_admin_new_user(context: ContextTypes.DEFAULT_TYPE, user) -> None:
    """إشعار المشرف الرئيسي مرة واحدة عند دخول عضو جديد للبوت."""
    if user is None or user.id == ADMIN_ID:
        return

    full_name = user.first_name or "غير معروف"
    if user.last_name:
        full_name += f" {user.last_name}"
    username = f"@{user.username}" if user.username else "لا يوجد"
    text = (
        "🆕 <b>مستخدم جديد دخل البوت</b>\n\n"
        f"👤 الاسم: {telegram_html(full_name)}\n"
        f"🆔 اليوزر: {telegram_html(username)}\n"
        f"🔢 ID: <code>{user.id}</code>"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ فشل إرسال إشعار دخول المستخدم {user.id}: {e}")


async def register_user_activity(context: ContextTypes.DEFAULT_TYPE, user) -> bool:
    """تحديث بيانات العضو وإرسال إشعار الدخول عند أول ظهور فقط."""
    if user is None:
        return False

    is_new = str(user.id) not in user_database
    update_user_info(user.id, user)
    if user.id == ADMIN_ID and user.username and not get_admin_contact_username():
        set_setting("admin_contact_username", user.username)
    if is_new:
        await asyncio.to_thread(increment_daily_stat, "new_users")
        await notify_admin_new_user(context, user)
    return is_new


async def notify_admin_subscription(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    channel_info,
) -> None:
    """إشعار المشرف مرة واحدة عند تحقق اشتراك العضو بالقناة الإجبارية."""
    if user_id == ADMIN_ID or not channel_info:
        return

    channel_username = str(channel_info.get("channel_username") or "").lstrip("@")
    channel_id = channel_info.get("channel_id")
    channel_key = str(channel_id or channel_username).lower()
    if not channel_key or subscription_notification_exists(user_id, channel_key):
        return

    info = user_database.get(str(user_id), {}) or {}
    full_name = info.get("first_name") or "غير معروف"
    if info.get("last_name"):
        full_name += f" {info['last_name']}"
    username = f"@{info.get('username')}" if info.get("username") else "لا يوجد"
    text = (
        "✅ <b>عضو اشترك بالقناة الإجبارية</b>\n\n"
        f"👤 الاسم: {telegram_html(full_name)}\n"
        f"🆔 اليوزر: {telegram_html(username)}\n"
        f"🔢 ID: <code>{user_id}</code>\n"
        f"📢 القناة: @{telegram_html(channel_username)}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
        mark_subscription_notified(user_id, channel_key)
    except Exception as e:
        print(f"⚠️ فشل إرسال إشعار اشتراك المستخدم {user_id}: {e}")


# ================== عرض البريد يدوياً فقط ==================
# لا توجد مهمة خلفية لإرسال رسائل البريد إلى محادثة البوت.
# المستخدم يفتح «الرسائل الواردة»، يختار الإيميل، ثم يقرأ رسائله.

# ================== النصوص العربية ==================

def get_text(_lang, key, **kwargs):
    texts = {
        "welcome": "🎉 مرحباً بك في بوت الإيميلات المؤقتة!",
        "main_menu": "📬 القائمة الرئيسية\n\nعدد الإيميلات النشطة: {emails_count}",
        "email_created": "✅ تم إنشاء بريد إلكتروني جديد!\n\n📧 الإيميل: <code>{email}</code>\n\nاضغط على الإيميل للنسخ",
        "no_emails": "❌ لا توجد إيميلات نشطة\n\nقم بإنشاء إيميل جديد أولاً",
        "select_email": "📋 اختر الإيميل:\n\nعدد الإيميلات: {count}",
        "no_messages": "📭 لا توجد رسائل\n\n📧 {email}",
        "messages_list": "📬 الرسائل الواردة ({count})\n📧 الإيميل: {email}\n\n",
        "message_detail": "✉️ تفاصيل الرسالة\n\n📧 من: {sender}\n📌 الموضوع: {subject}\n📅 التاريخ: {date}\n\n📝 المحتوى:\n{content}\n",
        "otp_found": "🔢 تم العثور على رمز OTP:\n\nالرمز: <code>{otp}</code>\n\nاضغط على الرمز للنسخ",
        "email_deleted": "🗑️ تم حذف الإيميل\n\n📧 {email}",
        "all_emails_deleted": "🗑️ تم حذف جميع الإيميلات ({count})",
        "error_create_email": "❌ فشل إنشاء الإيميل\n\nحاول مرة أخرى.",
        "error_load_messages": "❌ فشل تحميل الرسائل\n\nاضغط 🔄 تحديث للمحاولة.",
        "error_load_message": "❌ فشل تحميل الرسالة\n\nحاول لاحقاً.",
        "unauthorized": "⛔ هذا الأمر للمشرف فقط",
        "banned": "⛔ تم حظرك من استخدام البوت.",
        "btn_create": "✨ إنشاء إيميل جديد",
        "btn_my_emails": "📧 إيميلاتي",
        "btn_inbox": "📥 الرسائل الواردة",
        "btn_delete_all": "🗑️ حذف الكل",
        "btn_back": "🔙 رجوع",
        "btn_delete": "🗑️ حذف",
        "btn_confirm": "✅ تأكيد",
        "btn_cancel": "❌ إلغاء",
        "btn_refresh": "🔄 تحديث",
        "btn_admin_panel": "👑 لوحة المشرف",
    }
    value = texts.get(key, "")
    return value.format(**kwargs) if kwargs else value


# ================== لوحات الأزرار ==================

def get_main_menu_keyboard(_lang, user_id):
    keyboard = [
        [InlineKeyboardButton(get_text("ar", "btn_create"), callback_data="create_email", style="success")],
        [
            InlineKeyboardButton(get_text("ar", "btn_my_emails"), callback_data="my_emails", style="primary"),
            InlineKeyboardButton(get_text("ar", "btn_inbox"), callback_data="select_inbox", style="primary"),
        ],
        [InlineKeyboardButton("⚙️ إدارة إيميلاتك", callback_data="manage_emails", style="danger")],
    ]
    if is_admin(user_id):
        keyboard.append([
            InlineKeyboardButton(get_text("ar", "btn_admin_panel"), callback_data="admin_panel", style="primary")
        ])
    return InlineKeyboardMarkup(keyboard)


def get_free_domains_keyboard(domains):
    """عرض الدومينات المجانية النشطة التي تم جلبها مباشرة من mail.tm."""
    rows = []
    for index, domain in enumerate(domains):
        display_domain = str(domain)
        if len(display_domain) > 40:
            display_domain = display_domain[:37] + "..."
        rows.append([
            InlineKeyboardButton(
                f"@{display_domain}",
                callback_data=f"free_domain_{index}",
                style="primary",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🔄 تحديث الدومينات",
            callback_data="refresh_free_domains",
            style="success",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data="create_email",
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(rows)


def get_email_list_keyboard(emails, action_prefix, _lang):
    keyboard = []
    for index, email_info in enumerate(emails):
        email = email_info["address"]
        display_email = email if len(email) <= 30 else email[:27] + "..."
        keyboard.append([
            InlineKeyboardButton(
                f"📧 {display_email}",
                callback_data=f"{action_prefix}_{index}",
                style="primary",
            )
        ])
    keyboard.append([InlineKeyboardButton(get_text("ar", "btn_back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)


def get_messages_keyboard(messages, email_index, _lang):
    keyboard = []
    for index, message in enumerate(messages[:10]):
        subject = message.get("subject") or "بدون موضوع"
        display_subject = subject if len(subject) <= 30 else subject[:27] + "..."
        keyboard.append([
            InlineKeyboardButton(
                f"✉️ {display_subject}",
                callback_data=f"msg_{email_index}_{index}",
                style="primary",
            )
        ])
    keyboard.append([
        InlineKeyboardButton(get_text("ar", "btn_refresh"), callback_data=f"inbox_{email_index}", style="success"),
        InlineKeyboardButton(get_text("ar", "btn_back"), callback_data="select_inbox"),
    ])
    return InlineKeyboardMarkup(keyboard)


def get_admin_section_keyboard(buttons, back_callback="admin_panel"):
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data=back_callback,
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_panel_keyboard(_lang, user_id):
    keyboard = [
        [
            InlineKeyboardButton("📊 قسم الإحصائيات", callback_data="section_stats", style="primary"),
            InlineKeyboardButton("📢 قسم الإذاعة", callback_data="section_broadcast", style="primary"),
        ],
        [
            InlineKeyboardButton("📨 قسم توجيه الرسائل", callback_data="section_forward", style="primary"),
            InlineKeyboardButton("📢 إدارة القنوات", callback_data="channel_management", style="primary"),
        ],
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="section_settings", style="primary"),
            InlineKeyboardButton("👥 إدارة الأعضاء", callback_data="section_members", style="primary"),
        ],
        [
            InlineKeyboardButton("🔢 حد إنشاء الإيميلات", callback_data="section_email_limit", style="primary"),
            InlineKeyboardButton("🌐 إدارة الدومينات المدفوعة", callback_data="section_paid_domains", style="primary"),
        ],
        [InlineKeyboardButton("🩺 حالة البوت والخدمات", callback_data="section_health", style="primary")],
    ]

    if user_id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton("👮 إدارة المشرفين", callback_data="section_admins", style="primary"),
            InlineKeyboardButton("🛑 الحظر / فك الحظر", callback_data="section_ban", style="danger"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🛑 الحظر / فك الحظر", callback_data="section_ban", style="danger")
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("👋 رسالة الترحيب", callback_data="section_welcome", style="success"),
            InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info", style="primary"),
        ],
        [InlineKeyboardButton(get_text("ar", "btn_back"), callback_data="back_to_menu", style="primary")],
    ])
    return InlineKeyboardMarkup(keyboard)


def paginate_member_items(items, requested_page: int):
    total_items = len(items)
    total_pages = max(1, (total_items + MEMBERS_PAGE_SIZE - 1) // MEMBERS_PAGE_SIZE)
    page = min(max(0, int(requested_page)), total_pages - 1)
    start = page * MEMBERS_PAGE_SIZE
    return items[start:start + MEMBERS_PAGE_SIZE], page, total_pages


def get_member_pages_keyboard(prefix: str, page: int, total_pages: int):
    rows = []
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "⬅️ السابق", callback_data=f"{prefix}_{page - 1}", style="primary"
        ))
    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton(
            "التالي ➡️", callback_data=f"{prefix}_{page + 1}", style="primary"
        ))
    if navigation:
        rows.append(navigation)
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"), callback_data="section_members", style="primary"
        )
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_member_emails_view(target_id: int, requested_page: int = 0):
    """تجهيز شاشة إيميلات عضو للمشرف مع صفحات 10 إيميلات."""
    found = find_user_by_username_or_id(str(target_id))
    if not found:
        return None

    resolved_id, info = found
    emails = list(info.get("emails") or [])
    total_pages = max(1, (len(emails) + MEMBERS_PAGE_SIZE - 1) // MEMBERS_PAGE_SIZE)
    page = min(max(0, int(requested_page)), total_pages - 1)
    start = page * MEMBERS_PAGE_SIZE
    page_emails = emails[start:start + MEMBERS_PAGE_SIZE]

    name = info.get("first_name") or "مجهول"
    if info.get("last_name"):
        name += f" {info['last_name']}"
    username = f"@{info.get('username')}" if info.get("username") else "—"
    text = (
        "📧 إيميلات العضو\n\n"
        f"👤 الاسم: <b>{telegram_html(name)}</b>\n"
        f"🆔 اليوزر: {telegram_html(username)}\n"
        f"🔢 ID: <code>{resolved_id}</code>\n"
        f"📧 عدد الإيميلات: {len(emails)}\n"
        f"📄 الصفحة: {page + 1}/{total_pages}\n\n"
    )
    text += "اختر الإيميل لعرض بياناته وفتح البريد الوارد:" if emails else "لا توجد إيميلات محفوظة لهذا العضو."

    rows = []
    for offset, email_info in enumerate(page_emails):
        absolute_index = start + offset
        address = str(email_info.get("address") or "بريد غير معروف")
        display_address = address if len(address) <= 34 else address[:31] + "..."
        rows.append([
            InlineKeyboardButton(
                f"📧 {display_address}",
                callback_data=f"member_email_view_{resolved_id}_{absolute_index}_{page}",
                style="primary",
            )
        ])

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "⬅️ السابق",
            callback_data=f"member_emails_list_{resolved_id}_{page - 1}",
            style="primary",
        ))
    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton(
            "التالي ➡️",
            callback_data=f"member_emails_list_{resolved_id}_{page + 1}",
            style="primary",
        ))
    if navigation:
        rows.append(navigation)

    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data="section_members",
            style="primary",
        )
    ])
    return text, InlineKeyboardMarkup(rows)


def get_admin_member_messages_keyboard(messages, target_id: int, email_index: int, email_page: int):
    """أزرار رسائل وارد عضو للمشرف؛ القراءة تبقى يدوية فقط."""
    rows = []
    for index, item in enumerate((messages or [])[:10]):
        subject = item.get("subject") or "بدون موضوع"
        display_subject = subject if len(subject) <= 30 else subject[:27] + "..."
        rows.append([
            InlineKeyboardButton(
                f"✉️ {display_subject}",
                callback_data=f"member_msg_{target_id}_{email_index}_{index}_{email_page}",
                style="primary",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_refresh"),
            callback_data=f"member_inbox_{target_id}_{email_index}_{email_page}",
            style="success",
        ),
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data=f"member_email_view_{target_id}_{email_index}_{email_page}",
            style="primary",
        ),
    ])
    return InlineKeyboardMarkup(rows)


def build_admin_member_inbox_error_view(error_code, target_id: int, email_index: int, email_page: int, status=None):
    """خطأ وارد عضو للمشرف بدون أزرار حذف أو تغيير بيانات العضو."""
    if error_code == "token_invalid":
        text = (
            "⚠️ تعذر تجديد جلسة هذا البريد تلقائياً.\n\n"
            "قد تكون بيانات البريد قديمة أو لم يعد الحساب متاحاً على Mail.tm."
        )
    elif error_code == "rate_limited":
        text = "⏳ يوجد ضغط مؤقت على خدمة البريد.\n\nانتظر قليلاً ثم اضغط تحديث."
    elif error_code == "service_unavailable":
        text = "🛠️ خدمة البريد متوقفة أو تواجه مشكلة مؤقتة.\n\nحاول التحديث بعد قليل."
    elif error_code == "timeout":
        text = "⏱️ انتهت مهلة الاتصال بخدمة البريد.\n\nحاول التحديث لاحقاً."
    elif error_code in ("connection", "network"):
        text = "🌐 تعذر الاتصال بخدمة البريد مؤقتاً.\n\nحاول التحديث لاحقاً."
    elif error_code == "invalid_response":
        text = "⚠️ وصلت استجابة غير صالحة من خدمة البريد.\n\nحاول التحديث بعد قليل."
    elif error_code == "http_error" and status:
        text = f"⚠️ تعذر تحميل الرسائل بسبب خطأ HTTP {status}.\n\nحاول التحديث لاحقاً."
    else:
        text = get_text("ar", "error_load_messages")

    return text, InlineKeyboardMarkup([
        [InlineKeyboardButton(
            get_text("ar", "btn_refresh"),
            callback_data=f"member_inbox_{target_id}_{email_index}_{email_page}",
            style="success",
        )],
        [InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data=f"member_email_view_{target_id}_{email_index}_{email_page}",
            style="primary",
        )],
    ])


def get_channel_management_keyboard(_lang):
    channels = get_channels(only_enabled=False)
    rows = [[
        InlineKeyboardButton(
            "➕ إضافة قناة",
            callback_data="set_channel",
            style="success",
        ),
        InlineKeyboardButton(
            "✏️ تعديل رسالة الاشتراك",
            callback_data="edit_subscription_message",
            style="primary",
        ),
    ]]
    rows.append([
        InlineKeyboardButton(
            "📊 إحصائيات القنوات",
            callback_data="channel_stats",
            style="primary",
        )
    ])

    channel_buttons = []
    for channel in channels:
        status_icon = "✅" if channel.get("subscription_enabled") else "❌"
        username = str(channel.get("channel_username") or "")
        channel_buttons.append(
            InlineKeyboardButton(
                f"{status_icon} @{username}",
                callback_data=f"manage_channel_{channel['id']}",
                style="primary",
            )
        )
    rows.extend([
        channel_buttons[index:index + 2]
        for index in range(0, len(channel_buttons), 2)
    ])
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data="admin_panel",
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(rows)


# ================== أدوات منع/سماح (جديد) ==================
# ================== أدوات منع/سماح (جديد) ==================
# ================== أدوات منع/سماح (جديد) ==================

async def guard_user(update_or_query, context, user_id: int, lang: str) -> bool:
    """
    يرجع False إذا لازم نوقف (محظور/غير مشترك/البوت مطفي)
    """
    admin_user = is_admin(user_id)

    # محظور؟
    if not admin_user and is_banned(user_id):
        msg = get_text(lang, "banned")
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(msg)
        else:
            try:
                await update_or_query.edit_message_text(msg)
            except:
                pass
        return False

    # بوت مطفي؟
    if not bot_active and not admin_user:
        if bot_offline_message:
            prefix = "⚠️ البوت متوقف مؤقتاً\n\n"
            text = prefix + bot_offline_message
            rich_body = bot_offline_message_html or escape(bot_offline_message, quote=False)
            rich_text = escape(prefix, quote=False) + rich_body
        else:
            text = "⚠️ البوت متوقف مؤقتاً."
            rich_text = escape(text, quote=False)
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(rich_text, parse_mode="HTML")
        else:
            try:
                await update_or_query.edit_message_text(rich_text, parse_mode="HTML")
            except Exception:
                pass
        return False

    # اشتراك صارم بكل القنوات المفعّلة (لغير الأدمن)
    if not admin_user:
        active_channels = get_channels(only_enabled=True)
        missing_channels = await get_missing_subscription_channels(user_id, context)
        if missing_channels:
            text, kb = subscription_prompt(lang, missing_channels)
            if hasattr(update_or_query, "message") and update_or_query.message:
                await update_or_query.message.reply_text(
                    text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            else:
                try:
                    await update_or_query.edit_message_text(
                        text,
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return False

        for active_channel in active_channels:
            await notify_admin_subscription(context, user_id, active_channel)

    return True


# ================== أوامر ==================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    user_id = user.id
    await register_user_activity(context, user)
    lang = "ar"

    if not await guard_user(update, context, user_id, lang):
        return

    await message.reply_text(
        build_main_menu_html(user_id),
        reply_markup=get_main_menu_keyboard(lang, user_id),
        parse_mode="HTML",
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    user_id = user.id
    if not is_admin(user_id):
        await message.reply_text(get_text("ar", "unauthorized"))
        return
    await message.reply_text("👑 لوحة المشرف", reply_markup=get_admin_panel_keyboard("ar", user_id))


# ================== الأزرار ==================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_active, bot_offline_message

    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    user_id = user.id
    data = query.data or ""
    lang = "ar"

    cooldown_remaining = 0
    if data == "create_email_fast" or re.fullmatch(r"free_domain_\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "create_email", CREATE_EMAIL_COOLDOWN_SECONDS
        )
    elif data == "refresh_free_domains":
        cooldown_remaining = consume_action_cooldown(
            user_id, "free_domains", INBOX_COOLDOWN_SECONDS
        )
    elif re.fullmatch(r"inbox_\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "inbox", INBOX_COOLDOWN_SECONDS
        )
    elif re.fullmatch(r"member_inbox_\d+_\d+_\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "admin_member_inbox", INBOX_COOLDOWN_SECONDS
        )

    if cooldown_remaining > 0:
        try:
            await query.answer(
                f"⏳ انتظر {cooldown_remaining} ثانية قبل إعادة المحاولة.",
                show_alert=False,
            )
        except Exception:
            pass
        return

    if data == "verify_subscription":
        missing_channels = await get_missing_subscription_channels(user_id, context)
        if missing_channels:
            try:
                await query.answer(
                    "⚠️ يرجى الاشتراك بالقنوات لاستخدام البوت.",
                    show_alert=True,
                )
            except Exception:
                pass
            return

        try:
            await query.answer("✅ تم التحقق من الاشتراك.", show_alert=False)
        except Exception:
            pass
        for active_channel in get_channels(only_enabled=True):
            await notify_admin_subscription(context, user_id, active_channel)
        text = (
            "✅ تم التحقق من اشتراكك في جميع القنوات بنجاح!\n\n"
            + build_main_menu_html(user_id)
        )
        await query.edit_message_text(
            text,
            reply_markup=get_main_menu_keyboard(lang, user_id),
            parse_mode="HTML",
        )
        return

    try:
        await query.answer()
    except Exception:
        pass

    if not await guard_user(query, context, user_id, lang):
        return

    # رجوع للقائمة
    if data == "back_to_menu":
        await query.edit_message_text(
            build_main_menu_html(user_id),
            reply_markup=get_main_menu_keyboard(lang, user_id),
            parse_mode="HTML",
        )
        return

    # عرض الدومينات الشكلية المدفوعة للمستخدم
    if data == "change_domain":
        domains = get_paid_domains()
        if not domains:
            await query.edit_message_text(
                "💎 لا توجد دومينات مدفوعة متاحة حالياً.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )
                ]]),
            )
            return

        rows = []
        for index, domain in enumerate(domains):
            rows.append([
                InlineKeyboardButton(
                    f"@{domain}",
                    callback_data=f"paid_domain_{index}",
                    transparent=True,
                )
            ])
        rows.append([
            InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="create_email",
                style="primary",
            )
        ])
        await query.edit_message_text(
            "💎 الدومينات المدفوعة\n\nاختر أحد الدومينات المدفوعة المتاحة:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if re.fullmatch(r"paid_domain_\d+", data):
        domain_index = int(data.rsplit("_", 1)[1])
        domains = get_paid_domains()
        if domain_index >= len(domains):
            await query.edit_message_text(
                "⚠️ هذا الدومين لم يعد متاحاً.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="change_domain",
                        style="primary",
                    )
                ]]),
            )
            return

        domain = domains[domain_index]
        rows = []
        contact_username = get_admin_contact_username()
        if contact_username:
            rows.append([
                InlineKeyboardButton(
                    "💬 التواصل مع الأدمن",
                    url=f"https://t.me/{contact_username}",
                    style="success",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="change_domain",
                style="primary",
            )
        ])
        await query.edit_message_text(
            "💎 هذه الخدمة مدفوعة.\n\n"
            f"🌐 الدومين المختار: @{domain}\n\n"
            "يرجى التواصل مع الأدمن لاستخدام هذا الدومين.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    # إنشاء إيميل: يختار المستخدم بين الإنشاء السريع أو دومين مجاني محدد.
    if data == "create_email":
        current_count = len(get_user_emails(user_id))
        email_limit = get_effective_email_limit(user_id)
        if (not is_admin(user_id)) and email_limit > 0 and current_count >= email_limit:
            contact_username = get_admin_contact_username()
            rows = []
            if contact_username:
                rows.append([
                    InlineKeyboardButton(
                        "💬 التواصل مع الأدمن",
                        url=f"https://t.me/{contact_username}",
                        style="primary",
                    )
                ])
            rows.append([
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="back_to_menu",
                    style="primary",
                )
            ])
            await query.edit_message_text(
                "⚠️ لقد وصلت إلى الحد المسموح لإنشاء الإيميلات.\n\n"
                "يرجى التواصل مع الأدمن لإنشاء المزيد من الإيميلات.",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        text = (
            "✨ إنشاء إيميل جديد\n\n"
            "من هنا يمكنك اختيار طريقة إنشاء بريدك الإلكتروني.\n\n"
            "🎲 الإنشاء السريع:\n"
            "ينشئ لك البوت إيميل جديد مباشرة ويختار أحد\n"
            "الدومينات المجانية المتاحة تلقائياً.\n\n"
            "🌐 اختيار الدومين:\n"
            "اختر بنفسك أحد الدومينات المجانية المتاحة\n"
            "لإنشاء الإيميل عليه.\n\n"
            "💎 الدومينات المدفوعة:\n"
            "استعرض الدومينات المدفوعة المتوفرة واختر\n"
            "الدومين الذي ترغب باستخدامه."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎲 إنشاء سريع",
                        callback_data="create_email_fast",
                        style="success",
                    ),
                    InlineKeyboardButton(
                        "🌐 اختيار الدومين",
                        callback_data="select_free_domain",
                        style="primary",
                    ),
                ],
                [InlineKeyboardButton(
                    "💎 الدومينات المدفوعة",
                    callback_data="change_domain",
                    style="primary",
                )],
                [InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="back_to_menu",
                    style="primary",
                )],
            ]),
        )
        return

    if data == "create_email_fast":
        current_count = len(get_user_emails(user_id))
        email_limit = get_effective_email_limit(user_id)
        if (not is_admin(user_id)) and email_limit > 0 and current_count >= email_limit:
            await query.edit_message_text(
                "⚠️ لقد وصلت إلى الحد المسموح لإنشاء الإيميلات.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
            )
            return

        await query.edit_message_text(
            "🎲 إنشاء سريع\n\n"
            "جاري إنشاء إيميل جديد باستخدام أحد الدومينات المجانية المتاحة..."
        )
        email, token, password = await asyncio.to_thread(create_email)
        if email and token:
            add_user_email(user_id, email, token, password)
            await asyncio.to_thread(increment_daily_stat, "emails_created")
            await query.edit_message_text(
                get_text(lang, "email_created", email=telegram_html(email)),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                get_text(lang, "error_create_email"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 إعادة المحاولة",
                        callback_data="create_email_fast",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )],
                ]),
            )
        return

    if data in ("select_free_domain", "refresh_free_domains"):
        domains = await asyncio.to_thread(get_available_domains)
        if not domains:
            context.user_data.pop("free_domains", None)
            await query.edit_message_text(
                "🌐 اختيار الدومين\n\n"
                "تعذر تحميل الدومينات المجانية المتاحة حالياً.\n"
                "حاول التحديث بعد قليل.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 تحديث الدومينات",
                        callback_data="refresh_free_domains",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )],
                ]),
            )
            return

        context.user_data["free_domains"] = list(domains)
        await query.edit_message_text(
            "🌐 اختيار الدومين\n\n"
            "اختر أحد الدومينات المجانية المتاحة أدناه.\n"
            "بعد اختيار الدومين سيتم إنشاء إيميل جديد\n"
            "تلقائياً عليه.",
            reply_markup=get_free_domains_keyboard(domains),
        )
        return

    if re.fullmatch(r"free_domain_\d+", data):
        domain_index = int(data.rsplit("_", 1)[1])
        domains = list(context.user_data.get("free_domains") or [])
        if domain_index >= len(domains):
            domains = await asyncio.to_thread(get_available_domains)
            context.user_data["free_domains"] = list(domains)
            await query.edit_message_text(
                "🌐 اختيار الدومين\n\n"
                "تم تحديث قائمة الدومينات. اختر الدومين من جديد.",
                reply_markup=(
                    get_free_domains_keyboard(domains)
                    if domains
                    else InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "🔄 تحديث الدومينات",
                            callback_data="refresh_free_domains",
                            style="success",
                        )
                    ], [
                        InlineKeyboardButton(
                            get_text(lang, "btn_back"),
                            callback_data="create_email",
                            style="primary",
                        )
                    ]])
                ),
            )
            return

        current_count = len(get_user_emails(user_id))
        email_limit = get_effective_email_limit(user_id)
        if (not is_admin(user_id)) and email_limit > 0 and current_count >= email_limit:
            await query.edit_message_text(
                "⚠️ لقد وصلت إلى الحد المسموح لإنشاء الإيميلات.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
            )
            return

        domain = domains[domain_index]
        await query.edit_message_text(
            "🌐 إنشاء الإيميل\n\n"
            f"جاري إنشاء إيميل جديد على الدومين:\n@{domain}"
        )
        email, token, password = await asyncio.to_thread(create_email_with_domain, domain)
        if email and token:
            add_user_email(user_id, email, token, password)
            await asyncio.to_thread(increment_daily_stat, "emails_created")
            await query.edit_message_text(
                get_text(lang, "email_created", email=telegram_html(email)),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                "❌ فشل إنشاء الإيميل على الدومين المحدد.\n\n"
                "قد يكون الدومين لم يعد متاحاً، حدّث القائمة وحاول مرة أخرى.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 تحديث الدومينات",
                        callback_data="refresh_free_domains",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )],
                ]),
            )
        return

    # إيميلاتي
    if data == "my_emails":
        emails = get_user_emails(user_id)
        if not emails:
            await query.edit_message_text(get_text(lang, "no_emails"),
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        else:
            await query.edit_message_text(get_text(lang, "select_email", count=len(emails)),
                                          reply_markup=get_email_list_keyboard(emails, "view_email", lang))
        return

    # اختيار صندوق الوارد
    if data == "select_inbox":
        emails = get_user_emails(user_id)
        if not emails:
            await query.edit_message_text(get_text(lang, "no_emails"),
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        else:
            await query.edit_message_text(get_text(lang, "select_email", count=len(emails)),
                                          reply_markup=get_email_list_keyboard(emails, "inbox", lang))
        return

    # عرض صندوق وارد إيميل
    if data.startswith("inbox_"):
        idx_str = data.split("_", 1)[1]
        if not idx_str.isdigit():
            return
        email_index = int(idx_str)
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        await asyncio.to_thread(increment_daily_stat, "inbox_opens")
        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, user_id, email_index)
        messages = inbox_result.get("messages")

        if inbox_result.get("error") is not None:
            error_text, error_keyboard = build_inbox_error_view(
                inbox_result.get("error"),
                email_index,
                inbox_result.get("status"),
            )
            await query.edit_message_text(error_text, reply_markup=error_keyboard)
            return

        if len(messages) == 0:
            await query.edit_message_text(get_text(lang, "no_messages", email=email_data["address"]),
                                          reply_markup=InlineKeyboardMarkup([
                                              [InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}")],
                                              [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")]
                                          ]))
            return

        text = get_text(lang, "messages_list", count=len(messages), email=email_data["address"])
        await query.edit_message_text(text, reply_markup=get_messages_keyboard(messages, email_index, lang))
        return

    # تفاصيل رسالة
    if data.startswith("msg_"):
        parts = data.split("_")
        if len(parts) < 3 or (not parts[1].isdigit()) or (not parts[2].isdigit()):
            return
        email_index = int(parts[1])
        msg_index = int(parts[2])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]

        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, user_id, email_index)
        messages = inbox_result.get("messages")
        if inbox_result.get("error") is not None:
            error_text, error_keyboard = build_inbox_error_view(
                inbox_result.get("error"),
                email_index,
                inbox_result.get("status"),
            )
            await query.edit_message_text(error_text, reply_markup=error_keyboard)
            return
        if not messages or msg_index >= len(messages):
            return
        msg_id = messages[msg_index]["id"]

        full = await asyncio.to_thread(get_user_message_content, user_id, email_index, msg_id)
        if not full:
            await query.edit_message_text(get_text(lang, "error_load_message"),
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]))
            return

        sender_raw = (full.get("from") or {}).get("address") or "غير معروف"
        subject_raw = full.get("subject") or "بدون موضوع"
        date_raw = full.get("createdAt") or "غير معروف"
        content_raw = get_message_text(full)

        otp = extract_otp(content_raw)
        if len(content_raw) > 3500:
            content_raw = content_raw[:3500] + "\n\n... (الرسالة طويلة جداً)"

        safe_values = {
            "sender": telegram_html(sender_raw),
            "subject": telegram_html(subject_raw),
            "date": telegram_html(date_raw),
            "content": telegram_html(content_raw),
        }
        if otp:
            text = get_text(lang, "otp_found", otp=telegram_html(otp)) + "\n\n" + get_text(
                lang, "message_detail", **safe_values
            )
        else:
            text = get_text(lang, "message_detail", **safe_values)

        await query.edit_message_text(text,
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]),
                                      parse_mode="HTML")
        return

    # إدارة إيميلات المستخدم من قسم واحد بعيداً عن القائمة الرئيسية
    if data == "manage_emails":
        emails = get_user_emails(user_id)
        text = (
            "⚙️ إدارة إيميلاتك\n\n"
            f"📧 عدد إيميلاتك الحالية: {len(emails)}\n\n"
            "اختر العملية التي تريدها:"
        )
        rows = []
        if emails:
            rows.extend([
                [InlineKeyboardButton(
                    "🗑️ حذف إيميل محدد",
                    callback_data="manage_delete_one",
                    style="danger",
                )],
                [InlineKeyboardButton(
                    "🗑️ حذف كل الإيميلات",
                    callback_data="manage_confirm_delete_all",
                    style="danger",
                )],
            ])
        else:
            text += "\n\n📭 لا توجد إيميلات حالياً."
        rows.append([
            InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="back_to_menu",
                style="primary",
            )
        ])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "manage_delete_one":
        emails = get_user_emails(user_id)
        if not emails:
            await query.edit_message_text(
                get_text(lang, "no_emails"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="manage_emails",
                        style="primary",
                    )
                ]]),
            )
            return

        rows = []
        for email_index, email_info in enumerate(emails):
            address = str(email_info.get("address") or "بريد غير معروف")
            display_address = address if len(address) <= 34 else address[:31] + "..."
            rows.append([
                InlineKeyboardButton(
                    f"🗑️ {display_address}",
                    callback_data=f"manage_confirm_delete_{email_index}",
                    style="danger",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="manage_emails",
                style="primary",
            )
        ])
        await query.edit_message_text(
            "🗑️ اختر الإيميل الذي تريد حذفه:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if re.fullmatch(r"manage_confirm_delete_\d+", data):
        email_index = int(data.rsplit("_", 1)[1])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            await query.edit_message_text(
                "⚠️ هذا الإيميل لم يعد موجوداً.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="manage_delete_one",
                        style="primary",
                    )
                ]]),
            )
            return

        address = str(emails[email_index].get("address") or "غير معروف")
        text = (
            "⚠️ تأكيد حذف الإيميل\n\n"
            f"📧 {address}\n\n"
            "هل أنت متأكد؟"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ تأكيد الحذف",
                    callback_data=f"manage_delete_{email_index}",
                    style="success",
                ),
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="manage_delete_one",
                    style="danger",
                ),
            ]]),
        )
        return

    if re.fullmatch(r"manage_delete_\d+", data):
        email_index = int(data.rsplit("_", 1)[1])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        remove_user_email(user_id, email_data["address"])
        await query.edit_message_text(
            get_text(lang, "email_deleted", email=email_data["address"]),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="manage_emails",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "manage_confirm_delete_all":
        emails = get_user_emails(user_id)
        if not emails:
            await query.edit_message_text(
                get_text(lang, "no_emails"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="manage_emails",
                        style="primary",
                    )
                ]]),
            )
            return

        text = (
            "⚠️ تأكيد حذف جميع الإيميلات\n\n"
            f"📧 سيتم حذف جميع إيميلاتك الحالية: {len(emails)}\n\n"
            "⚠️ لا يمكن التراجع عن العملية بعد التأكيد."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ نعم، حذف الكل",
                    callback_data="manage_delete_all",
                    style="danger",
                ),
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="manage_emails",
                    style="primary",
                ),
            ]]),
        )
        return

    if data == "manage_delete_all":
        success, count = clear_user_emails(user_id)
        text = (
            get_text(lang, "all_emails_deleted", count=count)
            if success
            else "❌ فشل حذف الإيميلات"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="manage_emails",
                    style="primary",
                )
            ]]),
        )
        return

    # عرض تفاصيل إيميل
    if data.startswith("view_email_"):
        idx_str = data.split("_", 2)[2]
        if not idx_str.isdigit():
            return
        email_index = int(idx_str)
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        email_password = email_data.get("password") or LEGACY_MAIL_PASSWORD
        text = f"📧 <code>{email_data['address']}</code>\n🔑 <code>{telegram_html(email_password)}</code>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_text(lang, "btn_inbox"), callback_data=f"inbox_{email_index}", style="primary")],
            [InlineKeyboardButton(get_text(lang, "btn_delete"), callback_data=f"confirm_delete_{email_index}", style="danger")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="my_emails")]
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    # تأكيد حذف إيميل
    if re.fullmatch(r"confirm_delete_\d+", data):
        idx_str = data.split("_", 2)[2]
        if not idx_str.isdigit():
            return
        email_index = int(idx_str)
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        text = f"⚠️ هل أنت متأكد من حذف هذا الإيميل؟\n\n📧 {email_data['address']}"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data=f"delete_{email_index}", style="danger"),
            InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="my_emails")
        ]])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if re.fullmatch(r"delete_\d+", data):
        idx_str = data.split("_", 1)[1]
        if not idx_str.isdigit():
            return
        email_index = int(idx_str)
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        remove_user_email(user_id, email_data["address"])
        await query.edit_message_text(get_text(lang, "email_deleted", email=email_data["address"]),
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        return

    # حذف الكل
    if data == "confirm_delete_all":
        emails = get_user_emails(user_id)
        if not emails:
            await query.edit_message_text(get_text(lang, "no_emails"),
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
            return
        text = f"⚠️ هل أنت متأكد من حذف جميع الإيميلات؟\n\nالعدد: {len(emails)}"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data="delete_all", style="danger"),
            InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="back_to_menu")
        ]])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "delete_all":
        success, count = clear_user_emails(user_id)
        text = get_text(lang, "all_emails_deleted", count=count) if success else "❌ فشل حذف الإيميلات"
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")
            ]]),
        )
        return

    # ================== لوحة الأدمن (القديمة) ==================
    # ================== لوحة الأدمن (القديمة) ==================
    if data == "admin_panel":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return
        await query.edit_message_text("👑 لوحة تحكم المشرف\n\nاختر القسم:",
                                      reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    if data == "section_health":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return

        db_health, mail_health = await asyncio.gather(
            asyncio.to_thread(check_database_health),
            asyncio.to_thread(check_mail_service_health),
        )
        telegram_started = time.perf_counter()
        telegram_ok = True
        telegram_error = ""
        try:
            await context.bot.get_me()
        except Exception as error:
            telegram_ok = False
            telegram_error = str(error)[:120]
        telegram_ms = int((time.perf_counter() - telegram_started) * 1000)

        db_ok, db_ms, db_error = db_health
        mail_ok, mail_ms, mail_error = mail_health
        bot_status = "✅ يعمل" if bot_active else "⛔ متوقف للمستخدمين"
        telegram_status = f"✅ متصل ({telegram_ms} ms)" if telegram_ok else "❌ غير متصل"
        db_status = f"✅ متصلة ({db_ms} ms)" if db_ok else "❌ غير متصلة"
        mail_status = f"✅ متاحة ({mail_ms} ms)" if mail_ok else "❌ غير متاحة"

        errors = []
        if not telegram_ok:
            errors.append(f"تلجرام: {telegram_error}")
        if not db_ok:
            errors.append(f"قاعدة البيانات: {db_error}")
        if not mail_ok:
            errors.append(f"mail.tm: {mail_error}")
        errors_text = "\n".join(f"• {item}" for item in errors) if errors else "لا توجد أخطاء في الفحص الحالي."

        text = (
            "🩺 حالة البوت والخدمات\n\n"
            f"🤖 حالة البوت: {bot_status}\n"
            f"📨 اتصال تلجرام: {telegram_status}\n"
            f"🗄️ قاعدة البيانات: {db_status}\n"
            f"📧 خدمة mail.tm: {mail_status}\n"
            f"⏱️ مدة التشغيل: {format_bot_uptime()}\n\n"
            f"⚠️ نتيجة الأخطاء:\n{errors_text}"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تحديث الفحص", callback_data="section_health", style="success")],
                [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel", style="primary")],
            ]),
        )
        return

    if data == "channel_management":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return

        channels = get_channels(only_enabled=False)
        enabled_count = sum(1 for item in channels if item.get("subscription_enabled"))
        text = (
            "📢 إدارة قنوات الاشتراك الإجباري\n\n"
            f"📋 عدد القنوات المضافة: {len(channels)}\n"
            f"✅ القنوات المفعّلة: {enabled_count}\n\n"
        )
        if channels:
            text += "اضغط على أي قناة لإدارتها، أو أضف قناة جديدة."
        else:
            text += "لا توجد قنوات حالياً. أضف أول قناة للبدء."

        await query.edit_message_text(
            text,
            reply_markup=get_channel_management_keyboard(lang),
        )
        return


    if data == "channel_stats":
        if not is_admin(user_id):
            return

        channel_stats = await asyncio.to_thread(get_channel_subscription_stats)
        if channel_stats is None:
            await query.edit_message_text(
                "❌ تعذر تحميل إحصائيات القنوات حالياً.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
            return

        enabled_count = sum(1 for item in channel_stats if item.get("subscription_enabled"))
        disabled_count = len(channel_stats) - enabled_count
        total_verified = sum(int(item.get("verified_count") or 0) for item in channel_stats)
        text = (
            "📊 إحصائيات قنوات الاشتراك\n\n"
            f"📢 إجمالي القنوات: {len(channel_stats)}\n"
            f"✅ المفعّلة: {enabled_count}\n"
            f"❌ المعطّلة: {disabled_count}\n\n"
            f"👥 إجمالي عمليات الاشتراك التي تحقق منها البوت: {total_verified}\n"
        )

        if channel_stats:
            text += "\n━━━━━━━━━━━━━━\n"
            shown = 0
            for item in channel_stats:
                title = item.get("channel_title") or item.get("channel_username") or "غير محدد"
                username = str(item.get("channel_username") or "").lstrip("@")
                status = "✅ مفعّلة" if item.get("subscription_enabled") else "❌ معطّلة"
                verified_count = int(item.get("verified_count") or 0)
                block = (
                    f"\n📢 <b>{telegram_html(title)}</b>\n"
                    f"🔗 @{telegram_html(username)}\n"
                    f"⚙️ الحالة: {status}\n"
                    f"👥 تحقق البوت من اشتراك: {verified_count} عضو\n"
                )
                if len(text) + len(block) > 3800:
                    remaining = len(channel_stats) - shown
                    text += f"\n… ويوجد {remaining} قناة إضافية."
                    break
                text += block
                shown += 1
        else:
            text += "\nلا توجد قنوات مضافة حالياً."

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 تحديث الإحصائيات",
                    callback_data="channel_stats",
                    style="success",
                )],
                [InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )],
            ]),
        )
        return

    if data == "edit_subscription_message":
        if not is_admin(user_id):
            return
        current_message = get_global_subscription_message_html()
        context.user_data["waiting_for"] = "global_subscription_message"
        await query.edit_message_text(
            "✏️ تعديل رسالة الاشتراك الإجباري\n\n"
            "الرسالة الحالية:\n\n"
            f"{current_message}\n\n"
            "أرسل الرسالة الجديدة الآن.\n\n"
            "📌 قائمة القنوات ستظهر تلقائياً بين أول فقرة وباقي الرسالة.\n"
            "🌟 الإيموجي المميز يُحفظ تلقائياً عند إرساله داخل النص.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
            parse_mode="HTML",
        )
        return

    if data == "set_channel":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "channel_username"
        await query.edit_message_text(
            "➕ إضافة قناة للاشتراك الإجباري\n\n"
            "أرسل username القناة بدون @.\n"
            "مثال: mychannel\n\n"
            "يمكنك إضافة أكثر من قناة، ولن تُحذف القنوات السابقة.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
        )
        return

    if re.fullmatch(r"manage_channel_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if not channel_info:
            await query.edit_message_text(
                "❌ هذه القناة لم تعد موجودة.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
            return

        status = "✅ مفعّل" if channel_info.get("subscription_enabled") else "❌ معطّل"
        status_button = "❌ تعطيل الاشتراك" if channel_info.get("subscription_enabled") else "✅ تفعيل الاشتراك"
        status_style = "danger" if channel_info.get("subscription_enabled") else "success"
        cid = channel_info.get("channel_id", "غير محدد")
        title = channel_info.get("channel_title", "غير محدد")
        username = channel_info["channel_username"]
        text = (
            "📢 إدارة القناة\n\n"
            f"📢 الاسم: <b>{telegram_html(title)}</b>\n"
            f"🔗 القناة: @{telegram_html(username)}\n"
            f"🆔 المعرّف: <code>{cid}</code>\n"
            f"⚙️ الحالة: {status}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    status_button,
                    callback_data=f"toggle_subscription_{channel_db_id}",
                    style=status_style,
                ),
                InlineKeyboardButton(
                    "🗑 حذف القناة",
                    callback_data=f"delete_channel_{channel_db_id}",
                    style="danger",
                ),
            ],
            [InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="channel_management",
                style="primary",
            )],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if re.fullmatch(r"delete_channel_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if channel_info and delete_channel(channel_info["channel_username"]):
            await query.edit_message_text(
                f"✅ تم حذف القناة @{channel_info['channel_username']} بنجاح.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
        else:
            await query.edit_message_text(
                "❌ تعذر حذف القناة.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
        return

    if re.fullmatch(r"toggle_subscription_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if not channel_info:
            return
        new_status = toggle_subscription(channel_info["channel_username"])
        action = "تفعيل" if new_status else "تعطيل"
        await query.edit_message_text(
            f"✅ تم {action} الاشتراك الإجباري لقناة @{channel_info['channel_username']}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data=f"manage_channel_{channel_db_id}",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "delete_channel":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            delete_channel(channel_info["channel_username"])
            await query.edit_message_text(
                "✅ تم حذف القناة بنجاح",
                reply_markup=get_channel_management_keyboard(lang),
            )
        else:
            await query.edit_message_text(
                "❌ لا توجد قناة",
                reply_markup=get_channel_management_keyboard(lang),
            )
        return

    if data == "toggle_subscription":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            new_status = toggle_subscription(channel_info["channel_username"])
            action = "تفعيل" if new_status else "تعطيل"
            await query.edit_message_text(
                f"✅ تم {action} الاشتراك الإجباري",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data=f"manage_channel_{channel_info['id']}",
                        style="primary",
                    )
                ]]),
            )
        return

    # أقسام الأدمن القديمة الأساسية (موجودة ومفعلة)
    # أقسام الأدمن القديمة الأساسية (موجودة ومفعلة)
    if data == "section_stats":
        if not is_admin(user_id):
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📊 الإحصائيات العامة",
                    callback_data="stats_general",
                    transparent=True,
                ),
                InlineKeyboardButton(
                    "📈 إحصائيات الاستخدام اليومية",
                    callback_data="stats_daily",
                    transparent=True,
                ),
            ],
            [InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="admin_panel",
                style="primary",
            )],
        ])
        await query.edit_message_text(
            "📊 قسم الإحصائيات\n\nاختر نوع الإحصائيات التي تريد عرضها:",
            reply_markup=kb,
        )
        return

    if data == "stats_general":
        if not is_admin(user_id):
            return
        total_users = len(user_database)
        total_emails = sum(len(u.get("emails", [])) for u in user_database.values())
        active_users = sum(1 for u in user_database.values() if len(u.get("emails", [])) > 0)
        text = (
            "📊 الإحصائيات العامة\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"📧 إجمالي الإيميلات: {total_emails}\n"
            f"🔄 المستخدمون النشطون: {active_users}\n"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="section_stats",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "stats_daily":
        if not is_admin(user_id):
            return
        days = await asyncio.to_thread(get_last_seven_days_usage)
        if not days:
            text = "📈 إحصائيات الاستخدام اليومية\n\n❌ تعذر قراءة الإحصائيات حالياً."
        else:
            today = days[0]
            labels = ["اليوم", "أمس"]
            day_lines = []
            for index, item in enumerate(days):
                if index < len(labels):
                    label = labels[index]
                else:
                    label = item["stat_date"].strftime("%Y-%m-%d")
                day_lines.append(f"📅 {label}: {item['emails_created']} إيميل")

            best_day = max(days, key=lambda item: item["emails_created"])
            if best_day["stat_date"] == days[0]["stat_date"]:
                best_label = "اليوم"
            elif len(days) > 1 and best_day["stat_date"] == days[1]["stat_date"]:
                best_label = "أمس"
            else:
                best_label = best_day["stat_date"].strftime("%Y-%m-%d")

            text = (
                "📈 إحصائيات الاستخدام اليومية\n\n"
                "📅 إحصائيات اليوم:\n\n"
                f"👤 المستخدمون الجدد: {today['new_users']}\n"
                f"📧 الإيميلات المنشأة: {today['emails_created']}\n"
                f"📥 مرات فتح صندوق الوارد: {today['inbox_opens']}\n\n"
                "━━━━━━━━━━━━━━\n\n"
                "📊 آخر 7 أيام:\n\n"
                + "\n".join(day_lines)
                + "\n\n"
                "🏆 أعلى يوم استخدام خلال آخر 7 أيام:\n"
                f"{best_label}: {best_day['emails_created']} إيميل"
            )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 تحديث الإحصائيات",
                    callback_data="stats_daily",
                    style="success",
                )],
                [InlineKeyboardButton(
                    "🔙 رجوع إلى قسم الإحصائيات",
                    callback_data="section_stats",
                    style="primary",
                )],
            ]),
        )
        return

    if data == "section_forward":
        if not is_admin(user_id):
            return
        status = "✅ مفعّل" if forwarding_enabled else "❌ معطّل"
        text = (
            f"📨 قسم توجيه الرسائل\n\nالحالة: {status}\n\n"
            "عند التفعيل، كل ما يرسله المستخدم سيصلك كمحول منه مباشرة: "
            "الأوامر مثل /start، النصوص، الصور، الفيديو، الفويس، الملفات والملصقات."
        )
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("✅ تفعيل التوجيه", callback_data="forward_on", style="success"),
            InlineKeyboardButton("❌ تعطيل التوجيه", callback_data="forward_off", style="danger"),
        ], "admin_panel")
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "forward_on":
        if not is_admin(user_id):
            return
        if not set_setting("forwarding_enabled", "1"):
            await query.edit_message_text(
                "❌ تعذر حفظ حالة التوجيه في قاعدة البيانات.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]),
            )
            return
        forwarding_enabled = True
        await query.edit_message_text("✅ تم تفعيل توجيه الرسائل!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]))
        return

    if data == "forward_off":
        if not is_admin(user_id):
            return
        if not set_setting("forwarding_enabled", "0"):
            await query.edit_message_text(
                "❌ تعذر حفظ حالة التوجيه في قاعدة البيانات.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]),
            )
            return
        forwarding_enabled = False
        await query.edit_message_text("❌ تم تعطيل توجيه الرسائل!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]))
        return

    if data == "section_settings":
        if not is_admin(user_id):
            return
        status_icon = "✅" if bot_active else "❌"
        status_text = "يعمل" if bot_active else "متوقف"
        text = f"⚙️ الإعدادات\n\n• حالة البوت: {status_icon} {status_text}\n"
        if not bot_active and bot_offline_message:
            text += f"• رسالة الإيقاف: {bot_offline_message[:80]}..."
        kb = get_admin_section_keyboard([
            InlineKeyboardButton(f"🔄 حالة البوت: {status_icon}", callback_data="toggle_bot_status"),
            InlineKeyboardButton("✏️ رسالة الإيقاف", callback_data="set_offline_message"),
        ], "admin_panel")
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "toggle_bot_status":
        if not is_admin(user_id):
            return
        bot_active = not bot_active
        txt = "✅ تم تشغيل البوت!" if bot_active else "❌ تم إيقاف البوت!"
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]))
        return

    if data == "set_offline_message":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "offline_message"
        await query.edit_message_text("✏️ أرسل رسالة الإيقاف التي ستظهر للمستخدمين:",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]))
        return

    if data == "section_broadcast":
        if not is_admin(user_id):
            return
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("📨 إذاعة للكل", callback_data="broadcast_all", style="primary"),
            InlineKeyboardButton("👥 إذاعة للنشطين فقط", callback_data="broadcast_active", style="primary"),
        ], "admin_panel")
        await query.edit_message_text("📢 قسم الإذاعة\n\nاختر نوع الإذاعة:", reply_markup=kb)
        return

    if data == "broadcast_all":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "broadcast_all"
        await query.edit_message_text(f"📢 أرسل رسالة الإذاعة للكل\n\n⚠️ سيتم إرسالها لـ {len(user_database)} مستخدم",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]))
        return

    if data == "broadcast_active":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "broadcast_active"
        active_count = sum(1 for u in user_database.values() if len(u.get("emails", [])) > 0)
        await query.edit_message_text(f"📢 أرسل رسالة الإذاعة للنشطين فقط\n\n👥 النشطين: {active_count}",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]))
        return

    if data == "section_paid_domains":
        if not is_admin(user_id):
            return

        domains = get_paid_domains()
        text = "🌐 إدارة الدومينات المدفوعة\n\n"
        if domains:
            text += "الدومينات المعروضة للمستخدمين:\n"
            for index, domain in enumerate(domains, start=1):
                text += f"{index}. @{domain}\n"
        else:
            text += "لا توجد دومينات مضافة حالياً."

        buttons = [
            InlineKeyboardButton("➕ إضافة دومين", callback_data="add_paid_domain", style="success"),
        ]
        if domains:
            buttons.append(
                InlineKeyboardButton("🗑️ حذف دومين", callback_data="delete_paid_domain", style="danger")
            )
        await query.edit_message_text(
            text,
            reply_markup=get_admin_section_keyboard(buttons, "admin_panel"),
        )
        return

    if data == "add_paid_domain":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "paid_domain_add"
        await query.edit_message_text(
            "➕ أرسل اسم الدومين الذي تريد عرضه للمستخدمين.\n\nمثال: example.com",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="section_paid_domains",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "delete_paid_domain":
        if not is_admin(user_id):
            return
        domains = get_paid_domains()
        if not domains:
            await query.edit_message_text(
                "❌ لا توجد دومينات للحذف.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="section_paid_domains",
                        style="primary",
                    )
                ]]),
            )
            return

        buttons = [
            InlineKeyboardButton(
                f"🗑️ @{domain}",
                callback_data=f"remove_paid_domain_{index}",
                style="danger",
            )
            for index, domain in enumerate(domains)
        ]
        await query.edit_message_text(
            "🗑️ اختر الدومين الذي تريد حذفه:",
            reply_markup=get_admin_section_keyboard(buttons, "section_paid_domains"),
        )
        return

    if re.fullmatch(r"remove_paid_domain_\d+", data):
        if not is_admin(user_id):
            return
        domain_index = int(data.rsplit("_", 1)[1])
        success, removed_domain = remove_paid_domain(domain_index)
        result_text = (
            f"✅ تم حذف الدومين @{removed_domain}."
            if success
            else "❌ تعذر حذف الدومين."
        )
        await query.edit_message_text(
            result_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="section_paid_domains",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "section_email_limit":
        if not is_admin(user_id):
            return
        limit = get_email_limit()
        current = "غير محدود" if limit == 0 else str(limit)
        contact_username = get_admin_contact_username()
        contact_text = f"@{contact_username}" if contact_username else "غير محدد"
        buttons = [
            InlineKeyboardButton("✏️ تحديد العدد", callback_data="set_email_limit", style="primary"),
        ]
        if user_id == ADMIN_ID:
            buttons.append(
                InlineKeyboardButton(
                    "🎯 تحديد حد عضو عبر ID",
                    callback_data="set_member_email_limit",
                    style="primary",
                )
            )
        buttons.extend([
            InlineKeyboardButton(
                "👤 إضافة يوزر التواصل",
                callback_data="set_admin_contact_username",
                transparent=True,
            ),
            InlineKeyboardButton("♾️ إلغاء الحد", callback_data="clear_email_limit", style="danger"),
        ])
        await query.edit_message_text(
            "🔢 حد إنشاء الإيميلات\n\n"
            f"الحد الحالي لكل مستخدم: {current}\n"
            f"يوزر التواصل مع الأدمن: {contact_text}",
            reply_markup=get_admin_section_keyboard(buttons, "admin_panel"),
        )
        return

    if data == "set_email_limit":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "email_limit"
        await query.edit_message_text(
            "🔢 أرسل العدد الأقصى الذي يستطيع كل مستخدم إنشاءه.\n\nمثال: 3",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    if data == "set_member_email_limit":
        if user_id != ADMIN_ID:
            await query.answer("هذا الخيار للمشرف الرئيسي فقط.", show_alert=True)
            return
        context.user_data["waiting_for"] = "member_email_limit_id"
        context.user_data.pop("member_email_limit_target", None)
        await query.edit_message_text(
            "🎯 أرسل ID العضو فقط لتحديد الحد الخاص به.\n\nمثال: 123456789",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    if data == "set_admin_contact_username":
        if user_id != ADMIN_ID:
            await query.answer("هذا الإعداد للمشرف الرئيسي فقط.", show_alert=True)
            return
        context.user_data["waiting_for"] = "admin_contact_username"
        await query.edit_message_text(
            "👤 أرسل يوزر الأدمن الذي سيظهر للمستخدم عند وصوله للحد.\n\nمثال: @username",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    if data == "clear_email_limit":
        if not is_admin(user_id):
            return
        set_setting("email_limit", "0")
        await query.edit_message_text(
            "✅ تم إلغاء الحد وأصبح إنشاء الإيميلات غير محدود.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    if data == "section_members":
        if not is_admin(user_id):
            return
        total_users = len(user_database)
        active_users = sum(1 for u in user_database.values() if len(u.get("emails", [])) > 0)
        inactive_users = total_users - active_users
        total_emails = sum(len(u.get("emails", [])) for u in user_database.values())
        text = (
            "👥 إدارة الأعضاء\n\n"
            f"• إجمالي الأعضاء: {total_users}\n"
            f"• الأعضاء النشطون: {active_users}\n"
            f"• الأعضاء غير النشطين: {inactive_users}\n"
            f"• إجمالي الإيميلات: {total_emails}\n"
        )
        buttons = [
            InlineKeyboardButton("📋 قائمة كل الأعضاء", callback_data="users_list_all", style="primary"),
            InlineKeyboardButton("✅ الأعضاء النشطين", callback_data="users_list_active", style="success"),
            InlineKeyboardButton("🏆 الأكثر إيميلات", callback_data="users_list_top", style="primary"),
            InlineKeyboardButton("🔍 بحث عن عضو", callback_data="search_member", style="primary"),
            InlineKeyboardButton("📧 إيميلات عضو", callback_data="member_emails", style="primary"),
        ]
        if user_id == ADMIN_ID:
            buttons.append(
                InlineKeyboardButton(
                    "🗑️ حذف إيميلات عضو",
                    callback_data="delete_user_emails",
                    style="danger",
                )
            )
        await query.edit_message_text(
            text,
            reply_markup=get_admin_section_keyboard(buttons, "admin_panel"),
        )
        return

    if data == "users_list_all" or re.fullmatch(r"users_list_all_\d+", data):
        if not is_admin(user_id):
            return
        requested_page = int(data.rsplit("_", 1)[1]) if re.fullmatch(r"users_list_all_\d+", data) else 0
        members, page, total_pages = paginate_member_items(list(user_database.items()), requested_page)
        text = f"📋 قائمة كل الأعضاء — الصفحة {page + 1}/{total_pages}\n━━━━━━━━━━━━━━━\n\n"
        start_number = page * MEMBERS_PAGE_SIZE + 1
        for offset, (uid, info) in enumerate(members):
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            status = "✅" if emails_count > 0 else "⚪"
            text += f"{start_number + offset}. {status} <b>{telegram_html(name)}</b>\n    🆔 {telegram_html(username)} | 📧 {emails_count}\n    ID: <code>{uid}</code>\n\n"
        if not members:
            text += "لا يوجد أعضاء."
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=get_member_pages_keyboard("users_list_all", page, total_pages),
        )
        return

    if data == "users_list_active" or re.fullmatch(r"users_list_active_\d+", data):
        if not is_admin(user_id):
            return
        active_members = [(uid, info) for uid, info in user_database.items() if len(info.get("emails", [])) > 0]
        requested_page = int(data.rsplit("_", 1)[1]) if re.fullmatch(r"users_list_active_\d+", data) else 0
        members, page, total_pages = paginate_member_items(active_members, requested_page)
        text = f"✅ الأعضاء النشطين ({len(active_members)}) — الصفحة {page + 1}/{total_pages}\n━━━━━━━━━━━━━━━\n\n"
        start_number = page * MEMBERS_PAGE_SIZE + 1
        for offset, (uid, info) in enumerate(members):
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            text += f"{start_number + offset}. <b>{telegram_html(name)}</b>\n    🆔 {telegram_html(username)} | 📧 {emails_count}\n    ID: <code>{uid}</code>\n\n"
        if not members:
            text += "لا يوجد أعضاء نشطون."
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=get_member_pages_keyboard("users_list_active", page, total_pages),
        )
        return

    if data == "users_list_top" or re.fullmatch(r"users_list_top_\d+", data):
        if not is_admin(user_id):
            return
        sorted_users = sorted(
            ((uid, info) for uid, info in user_database.items() if len(info.get("emails", [])) > 0),
            key=lambda item: len(item[1].get("emails", [])), reverse=True,
        )
        requested_page = int(data.rsplit("_", 1)[1]) if re.fullmatch(r"users_list_top_\d+", data) else 0
        members, page, total_pages = paginate_member_items(sorted_users, requested_page)
        text = f"🏆 الأكثر إيميلات — الصفحة {page + 1}/{total_pages}\n━━━━━━━━━━━━━━━\n\n"
        start_rank = page * MEMBERS_PAGE_SIZE + 1
        medals = ["🥇", "🥈", "🥉"]
        for offset, (uid, info) in enumerate(members):
            rank = start_rank + offset
            medal = medals[rank - 1] if rank <= 3 else f"{rank}."
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            text += f"{medal} <b>{telegram_html(name)}</b>\n    🆔 {telegram_html(username)}\n    📧 {emails_count}\n    ID: <code>{uid}</code>\n\n"
        if not members:
            text += "لا توجد بيانات."
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=get_member_pages_keyboard("users_list_top", page, total_pages),
        )
        return

    if data == "member_emails":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "member_emails_id"
        context.user_data.pop("member_emails_target", None)
        await query.edit_message_text(
            "📧 عرض إيميلات عضو\n\nأرسل ID العضو الآن.\n\nمثال: 123456789",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="section_members",
                    style="primary",
                )
            ]]),
        )
        return

    match = re.fullmatch(r"member_emails_list_(\d+)_(\d+)", data)
    if match:
        if not is_admin(user_id):
            return
        target_id = int(match.group(1))
        page = int(match.group(2))
        view = get_admin_member_emails_view(target_id, page)
        if not view:
            await query.edit_message_text(
                "❌ لم يعد هذا العضو موجوداً في بيانات البوت.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members", style="primary")
                ]]),
            )
            return
        text, keyboard = view
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    match = re.fullmatch(r"member_email_view_(\d+)_(\d+)_(\d+)", data)
    if match:
        if not is_admin(user_id):
            return
        target_id = int(match.group(1))
        email_index = int(match.group(2))
        email_page = int(match.group(3))
        found = find_user_by_username_or_id(str(target_id))
        if not found:
            return
        _, info = found
        emails = list(info.get("emails") or [])
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        address = str(email_data.get("address") or "غير معروف")
        text = (
            "📧 بيانات إيميل العضو\n\n"
            f"🔢 ID العضو: <code>{target_id}</code>\n"
            f"📧 الإيميل: <code>{telegram_html(address)}</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📥 فتح البريد الوارد",
                callback_data=f"member_inbox_{target_id}_{email_index}_{email_page}",
                style="success",
            )],
            [InlineKeyboardButton(
                "🔙 رجوع إلى إيميلات العضو",
                callback_data=f"member_emails_list_{target_id}_{email_page}",
                style="primary",
            )],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    match = re.fullmatch(r"member_inbox_(\d+)_(\d+)_(\d+)", data)
    if match:
        if not is_admin(user_id):
            return
        target_id = int(match.group(1))
        email_index = int(match.group(2))
        email_page = int(match.group(3))
        found = find_user_by_username_or_id(str(target_id))
        if not found:
            return
        _, info = found
        emails = list(info.get("emails") or [])
        if email_index >= len(emails):
            return
        email_data = emails[email_index]

        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, target_id, email_index)
        messages = inbox_result.get("messages")
        if inbox_result.get("error") is not None:
            error_text, error_keyboard = build_admin_member_inbox_error_view(
                inbox_result.get("error"),
                target_id,
                email_index,
                email_page,
                inbox_result.get("status"),
            )
            await query.edit_message_text(error_text, reply_markup=error_keyboard)
            return

        address = str(email_data.get("address") or "غير معروف")
        if not messages:
            await query.edit_message_text(
                f"📭 لا توجد رسائل\n\n📧 {telegram_html(address)}\n🔢 ID العضو: <code>{target_id}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        get_text(lang, "btn_refresh"),
                        callback_data=f"member_inbox_{target_id}_{email_index}_{email_page}",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data=f"member_email_view_{target_id}_{email_index}_{email_page}",
                        style="primary",
                    )],
                ]),
            )
            return

        text = (
            f"📬 البريد الوارد للعضو ({len(messages)})\n"
            f"📧 الإيميل: {telegram_html(address)}\n"
            f"🔢 ID العضو: <code>{target_id}</code>\n\n"
            "اختر الرسالة لعرض محتواها:"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=get_admin_member_messages_keyboard(
                messages, target_id, email_index, email_page
            ),
        )
        return

    match = re.fullmatch(r"member_msg_(\d+)_(\d+)_(\d+)_(\d+)", data)
    if match:
        if not is_admin(user_id):
            return
        target_id = int(match.group(1))
        email_index = int(match.group(2))
        msg_index = int(match.group(3))
        email_page = int(match.group(4))

        found = find_user_by_username_or_id(str(target_id))
        if not found:
            return
        _, info = found
        emails = list(info.get("emails") or [])
        if email_index >= len(emails):
            return

        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, target_id, email_index)
        messages = inbox_result.get("messages")
        if inbox_result.get("error") is not None:
            error_text, error_keyboard = build_admin_member_inbox_error_view(
                inbox_result.get("error"),
                target_id,
                email_index,
                email_page,
                inbox_result.get("status"),
            )
            await query.edit_message_text(error_text, reply_markup=error_keyboard)
            return
        if not messages or msg_index >= len(messages):
            return

        msg_id = messages[msg_index].get("id")
        if not msg_id:
            return
        full = await asyncio.to_thread(get_user_message_content, target_id, email_index, msg_id)
        if not full:
            await query.edit_message_text(
                get_text(lang, "error_load_message"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data=f"member_inbox_{target_id}_{email_index}_{email_page}",
                        style="primary",
                    )
                ]]),
            )
            return

        sender_raw = (full.get("from") or {}).get("address") or "غير معروف"
        subject_raw = full.get("subject") or "بدون موضوع"
        date_raw = full.get("createdAt") or "غير معروف"
        content_raw = get_message_text(full)
        otp = extract_otp(content_raw)
        if len(content_raw) > 3500:
            content_raw = content_raw[:3500] + "\n\n... (الرسالة طويلة جداً)"

        safe_values = {
            "sender": telegram_html(sender_raw),
            "subject": telegram_html(subject_raw),
            "date": telegram_html(date_raw),
            "content": telegram_html(content_raw),
        }
        if otp:
            text = get_text(lang, "otp_found", otp=telegram_html(otp)) + "\n\n" + get_text(
                lang, "message_detail", **safe_values
            )
        else:
            text = get_text(lang, "message_detail", **safe_values)

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data=f"member_inbox_{target_id}_{email_index}_{email_page}",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "search_member":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "search_member"
        await query.edit_message_text("🔍 أرسل ID أو username أو اسم للبحث:",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    if data == "delete_user_emails":
        if user_id != ADMIN_ID:
            await query.answer("هذا الخيار للمشرف الرئيسي فقط.", show_alert=True)
            return
        context.user_data["waiting_for"] = "delete_user_emails"
        await query.edit_message_text(
            "🗑️ أرسل ID العضو أو @username لحذف جميع إيميلاته من بيانات البوت.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")
            ]]),
        )
        return

    if data.startswith("confirm_delete_user_emails_"):
        if user_id != ADMIN_ID:
            return
        target_value = data.replace("confirm_delete_user_emails_", "", 1)
        if not target_value.isdigit():
            return
        target_id = int(target_value)
        success, deleted_count = clear_user_emails(target_id)
        context.user_data.pop("delete_user_emails_target", None)
        if success:
            result_text = f"✅ تم حذف {deleted_count} إيميل من بيانات العضو."
        else:
            result_text = "❌ فشل حذف إيميلات العضو."
        await query.edit_message_text(
            result_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")
            ]]),
        )
        return

    # ================== إدارة المشرفين (للرئيسي فقط) ==================
    if data == "section_admins":
        if not is_admin(user_id):
            return
        if user_id != ADMIN_ID:
            await query.answer("هذا القسم للمشرف الرئيسي فقط!", show_alert=True)
            return

        admins = get_all_admins()
        text = "👮 إدارة المشرفين\n━━━━━━━━━━━━━━━\n\n"
        text += f"👑 المشرف الرئيسي: <code>{ADMIN_ID}</code>\n\n"
        if admins:
            text += f"👮 المشرفون الإضافيون ({len(admins)}):\n"
            for a in admins:
                name = a.get("first_name") or "مجهول"
                username = f"@{a.get('username')}" if a.get("username") else "—"
                text += f"• {telegram_html(name)} | {telegram_html(username)}\n  ID: <code>{a['telegram_id']}</code>\n"
        else:
            text += "لا يوجد مشرفون إضافيون حالياً\n"

        kb = get_admin_section_keyboard([
            InlineKeyboardButton("➕ إضافة مشرف", callback_data="add_admin", style="success"),
            InlineKeyboardButton("➖ إزالة مشرف", callback_data="remove_admin", style="danger"),
        ], "admin_panel")
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if data == "add_admin":
        if user_id != ADMIN_ID:
            return
        context.user_data["waiting_for"] = "add_admin"
        await query.edit_message_text("➕ أرسل ID أو @username لإضافة مشرف (لازم يكون استخدم البوت مسبقاً)",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]))
        return

    if data == "remove_admin":
        if user_id != ADMIN_ID:
            return
        admins = get_all_admins()
        if not admins:
            await query.edit_message_text("❌ لا يوجد مشرفون للإزالة",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]))
            return
        buttons = []
        for a in admins:
            name = a.get("first_name") or str(a["telegram_id"])
            buttons.append(
                InlineKeyboardButton(
                    f"❌ {name}",
                    callback_data=f"confirm_remove_admin_{a['telegram_id']}",
                    style="danger",
                )
            )
        await query.edit_message_text(
            "➖ اختر المشرف لإزالته:",
            reply_markup=get_admin_section_keyboard(buttons, "section_admins"),
        )
        return

    if data.startswith("confirm_remove_admin_"):
        if user_id != ADMIN_ID:
            return
        aid_str = data.replace("confirm_remove_admin_", "")
        if not aid_str.isdigit():
            return
        aid = int(aid_str)
        ok = remove_admin(aid)
        await query.edit_message_text("✅ تم إزالة المشرف" if ok else "❌ فشل إزالة المشرف",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]))
        return

    # ================== ✅ ميزاتك الجديدة (حظر/ترحيب) ==================
    if data == "section_ban":
        if not is_admin(user_id):
            return
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("🛑 حظر مستخدم", callback_data="ban_user", style="danger"),
            InlineKeyboardButton("✅ فك حظر مستخدم", callback_data="unban_user", style="success"),
        ], "admin_panel")
        await query.edit_message_text("🛑 قسم الحظر\n\nاختر:", reply_markup=kb)
        return

    if data == "ban_user":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "ban_user"
        await query.edit_message_text("🛑 أرسل ID المستخدم للحظر (مثال: 123456789)\nويمكنك تكتب سبب بالحظر بعده بسطر ثاني (اختياري).",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_ban")]]))
        return

    if data == "unban_user":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "unban_user"
        await query.edit_message_text("✅ أرسل ID المستخدم لفك الحظر:",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_ban")]]))
        return

    if data == "section_welcome":
        if not is_admin(user_id):
            return
        current, current_html = get_rich_text_setting("welcome_message", "")
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("✏️ تعيين رسالة الترحيب", callback_data="set_welcome_message", style="success"),
            InlineKeyboardButton("🧹 حذف رسالة الترحيب", callback_data="clear_welcome_message", style="danger"),
        ], "admin_panel")
        text = "👋 رسالة الترحيب الحالية:\n\n"
        text += (current_html if str(current or "").strip() else "— لا توجد رسالة —")
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if data == "set_welcome_message":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "welcome_message"
        await query.edit_message_text("✏️ أرسل رسالة الترحيب الجديدة. ستظهر مدمجة أعلى القائمة الرئيسية وعدد الإيميلات:",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]))
        return

    if data == "clear_welcome_message":
        if not is_admin(user_id):
            return
        set_setting("welcome_message", "")
        set_setting("welcome_message_rich_html", "")
        await query.edit_message_text("✅ تم حذف رسالة الترحيب",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]))
        return

    if data == "bot_info":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return
        text = "ℹ️ معلومات البوت\n\n🤖 الاسم: بوت الإيميلات المؤقتة\n📌 الإصدار: 3.1\n📧 الخدمة: mail.tm\n✅ الواجهة: العربية"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]))
        return


# ================== معالج الرسائل النصية (مثل كودك + إضافات انتظار الإدخال) ==================

async def forward_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توجيه كل رسالة خاصة من المستخدم للأدمن كـ Forward حقيقي قبل تنفيذ أي أمر أو منطق آخر."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not forwarding_enabled or user.id == ADMIN_ID:
        return

    try:
        await message.forward(chat_id=ADMIN_ID)
    except Exception as error:
        print(f"❌ فشل Forward الرسالة للأدمن: {error}")
        # احتياط عند منع تلجرام إعادة التوجيه لمحتوى معيّن حتى لا تضيع الرسالة.
        try:
            await message.copy(chat_id=ADMIN_ID)
        except Exception as copy_error:
            print(f"❌ فشل نسخ الرسالة للأدمن أيضاً: {copy_error}")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_offline_message, bot_offline_message_html

    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    user_id = user.id
    await register_user_activity(context, user)
    lang = "ar"

    # حارس منع
    if not await guard_user(update, context, user_id, lang):
        return

    waiting_for = context.user_data.get("waiting_for")
    if not waiting_for:
        return

    # تعيين قناة
    if waiting_for == "channel_username" and is_admin(user_id):
        channel_username = (update.message.text or "").strip().replace("@", "")
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            ok = set_channel(channel_username, chat.id, chat.title)
            text = (
                f"✅ تمت إضافة/تحديث القناة @{channel_username}\n"
                f"🆔 {chat.id}\n"
                f"📢 {chat.title}\n\n"
                "القنوات السابقة بقيت كما هي."
                if ok else "❌ فشل إضافة القناة"
            )
        except Exception as e:
            text = f"❌ خطأ: {str(e)[:200]}"
        context.user_data["waiting_for"] = None
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
        )
        return

    # تعديل رسالة الاشتراك الإجبارية العامة
    if waiting_for == "global_subscription_message" and is_admin(user_id):
        msg = update.message.text or ""
        if not msg.strip():
            await update.message.reply_text(
                "❌ الرسالة لا يمكن أن تكون فارغة.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
            return
        if len(msg) > 2500:
            await update.message.reply_text(
                "❌ الرسالة طويلة جداً. الحد الأقصى 2500 حرف.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
            return

        ok = save_rich_text_setting("global_subscription_message", message)
        if ok:
            context.user_data["waiting_for"] = None
        await update.message.reply_text(
            "✅ تم حفظ رسالة الاشتراك العامة." if ok else "❌ فشل حفظ الرسالة.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
        )
        return

    # رسالة الإيقاف
    # رسالة الإيقاف
    if waiting_for == "offline_message" and is_admin(user_id):
        bot_offline_message = update.message.text or ""
        bot_offline_message_html = message_custom_emoji_html(message)
        context.user_data["waiting_for"] = None
        await update.message.reply_text("✅ تم حفظ رسالة الإيقاف",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]))
        return

    # إذاعة للكل
    if waiting_for == "broadcast_all" and is_admin(user_id):
        context.user_data["waiting_for"] = None
        msg = update.message.text or ""
        msg_html = message_custom_emoji_html(message)
        wait_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة...")
        okc = 0
        fail = 0
        for uid in list(user_database.keys()):
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"📢 رسالة من الإدارة:\n\n{msg_html}",
                    parse_mode="HTML",
                )
                okc += 1
            except:
                fail += 1
        try:
            await wait_msg.delete()
        except:
            pass
        await update.message.reply_text(f"✅ تم الإرسال\nنجح: {okc}\nفشل: {fail}",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]))
        return

    # إذاعة للنشطين
    if waiting_for == "broadcast_active" and is_admin(user_id):
        context.user_data["waiting_for"] = None
        msg = update.message.text or ""
        msg_html = message_custom_emoji_html(message)
        wait_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة للنشطين...")
        okc = 0
        fail = 0
        for uid, info in user_database.items():
            if len(info.get("emails", [])) > 0:
                try:
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=f"📢 رسالة من الإدارة:\n\n{msg_html}",
                        parse_mode="HTML",
                    )
                    okc += 1
                except:
                    fail += 1
        try:
            await wait_msg.delete()
        except:
            pass
        await update.message.reply_text(f"✅ تم الإرسال\nنجح: {okc}\nفشل: {fail}",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]))
        return

    # إضافة دومين شكلي مدفوع من لوحة الأدمن
    if waiting_for == "paid_domain_add" and is_admin(user_id):
        success, result, domain = add_paid_domain(message.text or "")
        if not success:
            if result == "invalid":
                response_text = "❌ اسم الدومين غير صحيح. أرسله مثل example.com"
            elif result == "exists":
                response_text = f"⚠️ الدومين @{domain} مضاف مسبقاً."
            else:
                response_text = "❌ تعذر حفظ الدومين، حاول مرة أخرى."

            await message.reply_text(
                response_text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="section_paid_domains",
                        style="primary",
                    )
                ]]),
            )
            return

        context.user_data["waiting_for"] = None
        await message.reply_text(
            f"✅ تم إضافة الدومين الشكلي @{domain} للمستخدمين.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="section_paid_domains",
                    style="primary",
                )
            ]]),
        )
        return

    # تحديد الحد العام لإنشاء الإيميلات
    if waiting_for == "email_limit" and is_admin(user_id):
        raw_limit = (message.text or "").strip()
        if not raw_limit.isdigit() or not (1 <= int(raw_limit) <= 100):
            await message.reply_text(
                "❌ أرسل رقماً صحيحاً من 1 إلى 100.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
                ]]),
            )
            return

        set_setting("email_limit", raw_limit)
        context.user_data["waiting_for"] = None
        await message.reply_text(
            f"✅ تم تحديد الحد إلى {raw_limit} إيميل لكل مستخدم.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    # اختيار العضو عبر ID لتحديد حد خاص له - للمشرف الرئيسي فقط
    if waiting_for == "member_email_limit_id" and user_id == ADMIN_ID:
        raw_id = (message.text or "").strip()
        if not raw_id.isdigit():
            await message.reply_text(
                "❌ أرسل ID رقمي صحيح فقط، بدون @ أو username.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
                ]]),
            )
            return

        found = find_user_by_username_or_id(raw_id)
        if not found:
            await message.reply_text(
                "❌ لا يوجد عضو مسجل بهذا الـ ID.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
                ]]),
            )
            return

        target_id, target_info = found
        current_special = get_member_email_limit(target_id)
        current_global = get_email_limit()
        current_text = "غير محدد ويستخدم الحد العام" if current_special is None else (
            "غير محدود" if current_special == 0 else f"{current_special} إيميل"
        )
        global_text = "غير محدود" if current_global == 0 else f"{current_global} إيميل"
        name = target_info.get("first_name") or "مجهول"
        if target_info.get("last_name"):
            name += f" {target_info['last_name']}"

        context.user_data["member_email_limit_target"] = target_id
        context.user_data["waiting_for"] = "member_email_limit_value"
        await message.reply_text(
            "🎯 تحديد حد خاص للعضو\n\n"
            f"👤 العضو: {name}\n"
            f"🆔 ID: {target_id}\n"
            f"الحد الخاص الحالي: {current_text}\n"
            f"الحد العام: {global_text}\n\n"
            "أرسل الآن عدد الإيميلات المسموح به من 0 إلى 100.\n"
            "الرقم 0 يعني غير محدود لهذا العضو.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    # حفظ العدد الخاص بالعضو - للمشرف الرئيسي فقط
    if waiting_for == "member_email_limit_value" and user_id == ADMIN_ID:
        raw_limit = (message.text or "").strip()
        target_id = context.user_data.get("member_email_limit_target")
        if target_id is None:
            context.user_data["waiting_for"] = None
            await message.reply_text("❌ انتهت العملية، أعد المحاولة من لوحة الأدمن.")
            return
        if not raw_limit.isdigit() or not (0 <= int(raw_limit) <= 100):
            await message.reply_text(
                "❌ أرسل رقماً صحيحاً من 0 إلى 100.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
                ]]),
            )
            return

        limit_value = int(raw_limit)
        if not set_member_email_limit(int(target_id), limit_value):
            await message.reply_text("❌ تعذر حفظ الحد الخاص، حاول مرة أخرى.")
            return

        context.user_data["waiting_for"] = None
        context.user_data.pop("member_email_limit_target", None)
        limit_text = "غير محدود" if limit_value == 0 else f"{limit_value} إيميل"
        await message.reply_text(
            f"✅ تم تحديد حد العضو صاحب ID {target_id} إلى: {limit_text}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    # تحديد يوزر التواصل مع الأدمن - للمشرف الرئيسي فقط
    if waiting_for == "admin_contact_username" and user_id == ADMIN_ID:
        username = normalize_telegram_username(message.text or "")
        if not username:
            await message.reply_text(
                "❌ اليوزر غير صحيح. أرسله مثل @username ويتكوّن من أحرف إنجليزية أو أرقام أو _.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
                ]]),
            )
            return

        if not set_setting("admin_contact_username", username):
            await message.reply_text("❌ فشل حفظ يوزر الأدمن، حاول مرة أخرى.")
            return

        context.user_data["waiting_for"] = None
        await message.reply_text(
            f"✅ تم حفظ يوزر التواصل: @{username}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_email_limit")
            ]]),
        )
        return

    # عرض إيميلات عضو ووارده - للمشرفين فقط
    if waiting_for == "member_emails_id" and is_admin(user_id):
        raw_id = (message.text or "").strip()
        if not raw_id.isdigit():
            await message.reply_text(
                "❌ أرسل ID رقمي صحيح فقط.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="section_members",
                        style="primary",
                    )
                ]]),
            )
            return

        found = find_user_by_username_or_id(raw_id)
        if not found:
            await message.reply_text(
                "❌ لا يوجد عضو مسجل بهذا الـ ID.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="section_members",
                        style="primary",
                    )
                ]]),
            )
            return

        target_id, _ = found
        context.user_data["waiting_for"] = None
        context.user_data["member_emails_target"] = target_id
        view = get_admin_member_emails_view(target_id, 0)
        if not view:
            await message.reply_text("❌ تعذر تحميل بيانات العضو.")
            return
        text, keyboard = view
        await message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # اختيار عضو لحذف إيميلاته - للمشرف الرئيسي فقط
    if waiting_for == "delete_user_emails" and user_id == ADMIN_ID:
        found = find_user_by_username_or_id(message.text or "")
        if not found:
            await message.reply_text(
                "❌ لم يتم العثور على العضو. أرسل ID صحيحاً أو @username.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")
                ]]),
            )
            return

        target_id, target_info = found
        context.user_data["waiting_for"] = None
        context.user_data["delete_user_emails_target"] = target_id
        name = (target_info.get("first_name") or "مجهول")
        if target_info.get("last_name"):
            name += f" {target_info['last_name']}"
        username = f"@{target_info.get('username')}" if target_info.get("username") else "—"
        count = len(target_info.get("emails") or [])
        confirm_text = (
            "⚠️ تأكيد حذف إيميلات العضو\n\n"
            f"👤 الاسم: {name}\n"
            f"🆔 المستخدم: {username}\n"
            f"🔢 ID: {target_id}\n"
            f"📧 عدد الإيميلات: {count}\n\n"
            "سيتم حذف الإيميلات من بيانات البوت فقط، ولن يُحذف حساب العضو."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🗑️ تأكيد الحذف",
                callback_data=f"confirm_delete_user_emails_{target_id}",
                style="danger",
            ),
            InlineKeyboardButton("❌ إلغاء", callback_data="section_members", style="danger"),
        ]])
        await message.reply_text(confirm_text, reply_markup=keyboard)
        return

    # بحث عضو
    if waiting_for == "search_member" and is_admin(user_id):
        q = (update.message.text or "").strip().lower()
        context.user_data["waiting_for"] = None

        results = []
        for uid, info in user_database.items():
            if q in uid:
                results.append((uid, info))
                continue
            name = f"{info.get('first_name','')} {info.get('last_name','')}".lower()
            if q and q in name:
                results.append((uid, info))
                continue
            un = (info.get("username") or "").lower()
            if q and q in un:
                results.append((uid, info))

        if not results:
            await update.message.reply_text("❌ لم يتم العثور على عضو",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
            return

        text = f"🔍 نتائج البحث عن '{telegram_html(q)}':\n━━━━━━━━━━━━━━━\n\n"
        for uid, info in results[:10]:
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            status = "✅ نشط" if emails_count > 0 else "⚪ غير نشط"
            text += f"👤 <b>{telegram_html(name)}</b>\n🆔 {telegram_html(username)}\n📧 {emails_count} | {status}\n🔢 ID: <code>{uid}</code>\n\n"

        await update.message.reply_text(text, parse_mode="HTML",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    # ✅ تعيين رسالة الترحيب (جديد)
    if waiting_for == "welcome_message" and is_admin(user_id):
        msg = update.message.text or ""
        ok = save_rich_text_setting("welcome_message", message)
        if ok:
            context.user_data["waiting_for"] = None
        await update.message.reply_text(
            "✅ تم حفظ رسالة الترحيب والإيموجيات المميزة" if ok else "❌ فشل حفظ رسالة الترحيب",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]),
        )
        return

    # ✅ حظر مستخدم (جديد)
    if waiting_for == "ban_user" and is_admin(user_id):
        context.user_data["waiting_for"] = None
        raw = (update.message.text or "").strip()
        lines = raw.splitlines()
        try:
            target_id = int(lines[0].strip())
        except:
            await update.message.reply_text("❌ ارسل ID صحيح",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_ban")]]))
            return
        reason = lines[1].strip() if len(lines) > 1 else "—"
        ok = ban_user_db(target_id, reason, user_id)
        await update.message.reply_text("✅ تم حظر المستخدم" if ok else "❌ فشل الحظر",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_ban")]]))
        return

    # ✅ فك حظر مستخدم (جديد)
    if waiting_for == "unban_user" and is_admin(user_id):
        context.user_data["waiting_for"] = None
        try:
            target_id = int((update.message.text or "").strip())
        except:
            await update.message.reply_text("❌ ارسل ID صحيح",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_ban")]]))
            return
        ok = unban_user_db(target_id)
        await update.message.reply_text("✅ تم فك الحظر" if ok else "⚠️ المستخدم غير محظور أصلاً",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_ban")]]))
        return


# ================== معالج الأخطاء ==================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    error_text = str(error)
    ignorable = ("Query is too old", "query id is invalid", "Message is not modified")
    if any(item in error_text for item in ignorable):
        return

    print(f"❌ ERROR {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)


# ================== تشغيل ==================

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ ضع TELEGRAM_BOT_TOKEN في متغيرات Railway")
        return

    application = Application.builder().token(token).build()
    # المجموعة -1 تلتقط كل الرسائل الخاصة أولاً، بما فيها /start وباقي الأوامر والوسائط،
    # ثم تترك المعالجات الأصلية تنفذ وظائف البوت بشكل طبيعي.
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE, forward_incoming_message),
        group=-1,
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE,
        message_handler,
    ))
    application.add_error_handler(error_handler)

    # يبقى صندوق الوارد يدوياً: المستخدم يختار الإيميل ثم يفتح رسائله.
    # لا تُشغّل مهمة فحص كل الإيميلات تلقائياً حتى لا يفرض mail.tm حد HTTP 429.
    print("✅ فحص البريد يعمل يدوياً من زر الرسائل الواردة")

    print("🤖 البوت يعمل الآن...")
    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
