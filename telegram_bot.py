#!/usr/bin/env python3
"""
بوت تلجرام لإنشاء إيميلات مؤقتة متعددة (mail.tm)
+ لوحة مشرف
+ اشتراك إجباري قوي (يفحص كل مرة)
+ رسالة ترحيب قابلة للتعديل
+ حظر / فك حظر مستخدمين
"""

import os
import re
import json
import requests
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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

# حالة تشغيل البوت (الأدمن دايمًا يقدر يدخل)
bot_active = True
bot_offline_message = ""

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
                "lang": row.get("language"),
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


def save_single_user(telegram_id, user_info):
    conn = get_db_connection()
    if not conn:
        return
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
    except Exception as e:
        print(f"❌ خطأ في حفظ البيانات: {e}")
        conn.rollback()
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





# ---------- Delete Member (جديد) ----------
def delete_member_from_bot_users(telegram_id: int) -> bool:
    """يحذف المستخدم نهائياً من جدول bot_users. يرجع True إذا تم الحذف."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bot_users WHERE telegram_id=%s", (telegram_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"⚠️ خطأ في delete_member_from_bot_users: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
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


# ================== إدارة القنوات (مثل كودك) ==================

def get_channel_info(only_enabled=True):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if only_enabled:
                cur.execute("""
                    SELECT channel_username, channel_id, channel_title, subscription_message, subscription_enabled
                    FROM channels
                    WHERE subscription_enabled = TRUE
                    LIMIT 1
                """)
            else:
                cur.execute("""
                    SELECT channel_username, channel_id, channel_title, subscription_message, subscription_enabled
                    FROM channels
                    ORDER BY created_at DESC
                    LIMIT 1
                """)
            return cur.fetchone()
    except Exception as e:
        print(f"❌ خطأ في الحصول على معلومات القناة: {e}")
        return None
    finally:
        conn.close()


def set_channel(channel_username, channel_id=None, channel_title=None):
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
        print(f"❌ خطأ في تعيين القناة: {e}")
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
            conn.commit()
            return True
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


# ================== اشتراك إجباري قوي (جديد/محسن) ==================

async def check_user_subscription_strict(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    قوي: يفحص كل مرة - إذا خرج من القناة يرجع False فورًا
    """
    channel_info = get_channel_info()
    if not channel_info:
        return True

    if not channel_info.get("subscription_enabled"):
        return True

    channel_username = channel_info["channel_username"]
    channel_id = channel_info.get("channel_id")
    chat_identifier = channel_id if channel_id else f"@{channel_username}"

    try:
        member = await context.bot.get_chat_member(chat_identifier, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        # إذا في مشكلة بالقناة نفسها، الأفضل منع (حتى يكون "صارم")
        print(f"⚠️ check_user_subscription_strict error: {e}")
        return False


def subscription_prompt(lang: str, channel_username: str, message: str):
    text_ar = (
        "⚠️ يجب عليك الاشتراك في القناة للاستخدام\n\n"
        f"🔗 القناة: @{channel_username}\n\n"
        f"{message}\n\n"
        "بعد الاشتراك اضغط: ✅ التحقق من الاشتراك"
    )
    text_en = (
        "⚠️ You must join the channel to use the bot\n\n"
        f"🔗 Channel: @{channel_username}\n\n"
        f"{message}\n\n"
        "After joining press: ✅ Verify Subscription"
    )
    text = text_ar if lang == "ar" else text_en

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 الانضمام للقناة" if lang == "ar" else "📢 Join Channel",
                              url=f"https://t.me/{channel_username}")],
        [InlineKeyboardButton("✅ التحقق من الاشتراك" if lang == "ar" else "✅ Verify Subscription",
                              callback_data="verify_subscription")]
    ])
    return text, kb


# ================== mail.tm API (مثل كودك) ==================

def get_available_domains():
    try:
        r = requests.get(f"{API}/domains", timeout=10)
        if r.status_code == 200:
            data = r.json()
            domains = data.get("hydra:member", [])
            return [d["domain"] for d in domains] if domains else []
    except Exception as e:
        print(f"⚠️ get_available_domains: {e}")
    return []


def create_email():
    try:
        domains = get_available_domains()
        if not domains:
            return None, None

        import random, string
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email_address = f"{username}@{domains[0]}"
        password = "TempMail123"

        r = requests.post(f"{API}/accounts", json={"address": email_address, "password": password}, timeout=10)
        if r.status_code != 201:
            return None, None

        tr = requests.post(f"{API}/token", json={"address": email_address, "password": password}, timeout=10)
        if tr.status_code != 200:
            return None, None

        token = tr.json().get("token")
        return (email_address, token) if token else (None, None)
    except Exception as e:
        print(f"❌ create_email: {e}")
        return None, None


def check_inbox(token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"{API}/messages", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("hydra:member", [])
        return None
    except Exception as e:
        print(f"⚠️ check_inbox: {e}")
        return None


def get_message_content(message_id, token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"{API}/messages/{message_id}", headers=headers, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"⚠️ get_message_content: {e}")
        return None


def extract_otp(text):
    if not text:
        return None
    m = re.search(r"\b(\d{4,8})\b", text)
    return m.group(1) if m else None

def html_to_text(html: str) -> str:
    """تحويل HTML لنص بسيط (بدون مكتبات خارجية)."""
    if not html:
        return ""
    # إزالة script/style
    html = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", ' ', html, flags=re.IGNORECASE | re.DOTALL)
    # استبدال بعض الوسوم بسطر جديد
    html = re.sub(r"<\s*br\s*/?>", '\n', html, flags=re.IGNORECASE)
    html = re.sub(r"</\s*p\s*>", '\n', html, flags=re.IGNORECASE)
    # إزالة بقية الوسوم
    html = re.sub(r"<[^>]+>", ' ', html)
    # فك بعض الـ entities
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # تنظيف
    html = re.sub(r"[\t\r ]+", ' ', html)
    html = re.sub(r"\n{3,}", '\n\n', html)
    return html.strip()


def get_message_text(full: dict) -> str:
    """يرجع أفضل نص متاح من رسالة mail.tm."""
    if not full:
        return ""
    txt = (full.get("text") or "").strip()
    if txt:
        return txt
    intro = (full.get("intro") or "").strip()
    if intro:
        return intro
    html = (full.get("html") or "").strip()
    if html:
        return html_to_text(html)
    return ""



# ================== بيانات المستخدمين (مثل كودك) ==================

init_database()
user_database = load_user_data()

def get_user_data(user_id):
    uid = str(user_id)
    if uid not in user_database:
        user_database[uid] = {"lang": None, "emails": []}
        save_single_user(uid, user_database[uid])
    return user_database[uid]

def get_user_emails(user_id):
    return get_user_data(user_id).get("emails", [])

def get_user_language(user_id):
    return get_user_data(user_id).get("lang")

def update_user_info(user_id, user):
    data = get_user_data(user_id)
    data["first_name"] = user.first_name or ""
    data["last_name"] = user.last_name or ""
    data["username"] = user.username or ""
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)

def set_user_language(user_id, lang, user=None):
    data = get_user_data(user_id)
    data["lang"] = lang
    if user:
        data["first_name"] = user.first_name or ""
        data["last_name"] = user.last_name or ""
        data["username"] = user.username or ""
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)

def add_user_email(user_id, email, token):
    data = get_user_data(user_id)
    data["emails"].append({"address": email, "token": token})
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)

def remove_user_email(user_id, email):
    data = get_user_data(user_id)
    data["emails"] = [e for e in data.get("emails", []) if e.get("address") != email]
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)


# ================== مزامنة الرسائل تلقائياً (جديد) ==================

async def poll_inboxes_job(context: ContextTypes.DEFAULT_TYPE):
    """    يفحص صناديق الوارد لكل الإيميلات المحفوظة ويرسل أي رسالة جديدة تلقائياً.

    - يحفظ آخر رسالة مرسلة لكل بريد داخل جدول email_seen.
    - لا يغيّر أي أزرار/ميزات قديمة، فقط إضافة خدمة خلفية.
    """
    global user_database

    # تحميل أحدث نسخة من المستخدمين (لو صار تعديل أثناء التشغيل)
    # (بدون ضغط كبير: نكتفي بالنسخة الموجودة بالRAM؛ إذا تحتاج مزامنة حقيقية أخبرني)

    for uid_str, info in list(user_database.items()):
        try:
            user_id = int(uid_str)
        except Exception:
            continue

        emails = (info or {}).get('emails') or []
        if not emails:
            continue

        # لا نرسل للمحظورين
        if (not is_admin(user_id)) and is_banned(user_id):
            continue

        for e in emails:
            address = (e or {}).get('address')
            token = (e or {}).get('token')
            if not address or not token:
                continue

            last_seen = get_last_seen_message_id(address)
            msgs = check_inbox(token) or []
            if not msgs:
                continue

            # mail.tm غالباً يعيد الأحدث أولاً
            new_msgs = []
            for m in msgs:
                mid = m.get('id')
                if not mid:
                    continue
                if last_seen and mid == last_seen:
                    break
                new_msgs.append(m)

            if not new_msgs:
                continue

            # نرسل الأقدم أولاً
            new_msgs = list(reversed(new_msgs))[:5]

            lang = get_user_language(user_id) or 'ar'

            for m in new_msgs:
                mid = m.get('id')
                if not mid:
                    continue

                full = get_message_content(mid, token) or {}

                sender = (full.get('from') or {}).get('address') or 'Unknown'
                subject = full.get('subject') or 'No Subject'
                date = full.get('createdAt') or ''
                content = get_message_text(full)

                if len(content) > 3500:
                    content = content[:3500] + ("\n\n... (الرسالة طويلة جداً)" if lang == "ar" else "\n\n... (too long)")

                otp = extract_otp(content)

                header = '📩 وصلت رسالة جديدة' if lang == 'ar' else '📩 New message arrived'
                from_line = f"📧 من: {sender}" if lang == 'ar' else f"📧 From: {sender}"
                subj_line = f"📌 الموضوع: {subject}" if lang == 'ar' else f"📌 Subject: {subject}"
                content_title = '📝 المحتوى:' if lang == 'ar' else '📝 Content:'

                parts = [
                    header,
                    f"📧 {address}",
                    from_line,
                    subj_line,
                ]
                if date:
                    parts.append(f"📅 {date}")
                if otp:
                    parts.append(f"🔢 OTP: <code>{otp}</code>")
                parts.append('')
                parts.append(content_title)
                parts.append(content)

                msg_text = "\n".join(parts)

                # نرسل HTML لكي كود OTP يظهر واضح
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=msg_text,
                        parse_mode='HTML',
                        disable_web_page_preview=True,
                    )
                except Exception:
                    # إذا فشل HTML لأي سبب، نرسل كنص عادي
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=re.sub(r'<\/?.*?>', '', msg_text),
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        pass

                # حدث آخر رسالة مرسلة لهذا البريد
                set_last_seen_message_id(address, mid)

            # تأكيد آخر رسالة هي الأحدث (حماية)
            newest_id = msgs[0].get('id')
            if newest_id:
                set_last_seen_message_id(address, newest_id)


# ================== نصوص (مختصر) ==================

def get_text(lang, key, **kwargs):
    texts = {
        "ar": {
            "welcome": "🎉 مرحباً بك في بوت الإيميلات المؤقتة!\n\nاختر لغتك المفضلة:",
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
            "btn_language": "🌐 تغيير اللغة",
            "btn_back": "🔙 رجوع",
            "btn_delete": "🗑️ حذف",
            "btn_confirm": "✅ تأكيد",
            "btn_cancel": "❌ إلغاء",
            "btn_refresh": "🔄 تحديث",
            "btn_admin_panel": "👑 لوحة المشرف",
        },
        "en": {
            "welcome": "🎉 Welcome!\n\nChoose your language:",
            "main_menu": "📬 Main Menu\n\nActive emails: {emails_count}",
            "email_created": "✅ New email created!\n\n📧 Email: <code>{email}</code>\n\nTap to copy",
            "no_emails": "❌ No active emails\n\nCreate one first",
            "select_email": "📋 Select email:\n\nCount: {count}",
            "no_messages": "📭 No messages\n\n📧 {email}",
            "messages_list": "📬 Inbox ({count})\n📧 Email: {email}\n\n",
            "message_detail": "✉️ Message\n\n📧 From: {sender}\n📌 Subject: {subject}\n📅 Date: {date}\n\n📝 Content:\n{content}\n",
            "otp_found": "🔢 OTP found:\n\nCode: <code>{otp}</code>",
            "email_deleted": "🗑️ Email deleted\n\n📧 {email}",
            "all_emails_deleted": "🗑️ Deleted all emails ({count})",
            "error_create_email": "❌ Failed to create email\n\nTry again.",
            "error_load_messages": "❌ Failed to load messages\n\nPress 🔄 Refresh.",
            "error_load_message": "❌ Failed to load message\n\nTry later.",
            "unauthorized": "⛔ Admin only",
            "banned": "⛔ You are banned from using this bot.",
            "btn_create": "✨ Create New Email",
            "btn_my_emails": "📧 My Emails",
            "btn_inbox": "📥 Inbox",
            "btn_delete_all": "🗑️ Delete All",
            "btn_language": "🌐 Change Language",
            "btn_back": "🔙 Back",
            "btn_delete": "🗑️ Delete",
            "btn_confirm": "✅ Confirm",
            "btn_cancel": "❌ Cancel",
            "btn_refresh": "🔄 Refresh",
            "btn_admin_panel": "👑 Admin Panel",
        }
    }
    t = texts.get(lang, texts["ar"]).get(key, "")
    return t.format(**kwargs) if kwargs else t


# ================== Keyboards (مثل كودك + إضافة إدارة الحظر/الترحيب بالأدمن) ==================

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ])

def get_main_menu_keyboard(lang, user_id):
    keyboard = [
        [InlineKeyboardButton(get_text(lang, "btn_create"), callback_data="create_email")],
        [
            InlineKeyboardButton(get_text(lang, "btn_my_emails"), callback_data="my_emails"),
            InlineKeyboardButton(get_text(lang, "btn_inbox"), callback_data="select_inbox"),
        ],
        [InlineKeyboardButton(get_text(lang, "btn_delete_all"), callback_data="confirm_delete_all")],
        [InlineKeyboardButton(get_text(lang, "btn_language"), callback_data="change_language")],
    ]
    if is_admin(user_id):
        keyboard.insert(3, [InlineKeyboardButton(get_text(lang, "btn_admin_panel"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_email_list_keyboard(emails, action_prefix, lang):
    keyboard = []
    for i, e in enumerate(emails):
        email = e["address"]
        display_email = email if len(email) <= 30 else email[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"📧 {display_email}", callback_data=f"{action_prefix}_{i}")])
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_messages_keyboard(messages, email_index, lang):
    keyboard = []
    for i, msg in enumerate(messages[:10]):
        subject = msg.get("subject", "No Subject")
        display_subject = subject if len(subject) <= 30 else subject[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"✉️ {display_subject}", callback_data=f"msg_{email_index}_{i}")])
    keyboard.append([
        InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}"),
        InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox"),
    ])
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel_keyboard(lang, user_id):
    # نفس أزرارك القديمة + أزرار جديدة (من غير حذف القديم)
    keyboard = [
        [InlineKeyboardButton("📊 قسم الإحصائيات" if lang=="ar" else "📊 Statistics", callback_data="section_stats")],
        [InlineKeyboardButton("📢 قسم الإذاعة" if lang=="ar" else "📢 Broadcasting", callback_data="section_broadcast")],
        [InlineKeyboardButton("📨 قسم توجيه الرسائل" if lang=="ar" else "📨 Message Forwarding", callback_data="section_forward")],
        [InlineKeyboardButton("📢 إدارة القنوات" if lang=="ar" else "📢 Channel Management", callback_data="channel_management")],
        [InlineKeyboardButton("⚙️ الإعدادات" if lang=="ar" else "⚙️ Settings", callback_data="section_settings")],
        [InlineKeyboardButton("👥 إدارة الأعضاء" if lang=="ar" else "👥 Member Management", callback_data="section_members")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮 إدارة المشرفين" if lang=="ar" else "👮 Admin Management", callback_data="section_admins")])

    # ✅ إضافاتك الجديدة للأدمن
    keyboard.append([InlineKeyboardButton("🛑 الحظر / فك الحظر" if lang=="ar" else "🛑 Ban / Unban", callback_data="section_ban")])
    keyboard.append([InlineKeyboardButton("👋 رسالة الترحيب" if lang=="ar" else "👋 Welcome Message", callback_data="section_welcome")])

    keyboard.append([InlineKeyboardButton("ℹ️ معلومات البوت" if lang=="ar" else "ℹ️ Bot Info", callback_data="bot_info")])
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_channel_management_keyboard(lang):
    channel_info = get_channel_info(only_enabled=False)
    keyboard = [
        [InlineKeyboardButton("تعيين القناة" if lang=="ar" else "Set Channel", callback_data="set_channel")],
        [InlineKeyboardButton("تعيين رسالة الاشتراك" if lang=="ar" else "Set Subscription Message", callback_data="set_channel_message")],
    ]
    if channel_info:
        status_icon = "✅" if channel_info.get("subscription_enabled") else "❌"
        keyboard.append([InlineKeyboardButton(f"إشعار الاشتراك: {status_icon}" if lang=="ar" else f"Subscription: {status_icon}",
                                             callback_data="toggle_subscription")])
        keyboard.append([InlineKeyboardButton("حذف القناة" if lang=="ar" else "Delete Channel", callback_data="delete_channel")])
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)


# ================== أدوات منع/سماح (جديد) ==================

async def guard_user(update_or_query, context, user_id: int, lang: str) -> bool:
    """
    يرجع False إذا لازم نوقف (محظور/غير مشترك/البوت مطفي)
    """
    # محظور؟
    if not is_admin(user_id) and is_banned(user_id):
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
    if not bot_active and not is_admin(user_id):
        text = f"⚠️ البوت متوقف مؤقتاً\n\n{bot_offline_message}" if bot_offline_message else "⚠️ البوت متوقف مؤقتاً."
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(text)
        else:
            try:
                await update_or_query.edit_message_text(text)
            except:
                pass
        return False

    # اشتراك صارم (لغير الأدمن)
    if not is_admin(user_id):
        ok = await check_user_subscription_strict(user_id, context)
        if not ok:
            ch = get_channel_info()
            if ch:
                msg = ch.get("subscription_message") or ""
                text, kb = subscription_prompt(lang, ch["channel_username"], msg)
                if hasattr(update_or_query, "message") and update_or_query.message:
                    await update_or_query.message.reply_text(text, reply_markup=kb)
                else:
                    try:
                        await update_or_query.edit_message_text(text, reply_markup=kb)
                    except:
                        pass
            return False

    return True


# ================== أوامر ==================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user

    update_user_info(user_id, user)
    lang = get_user_language(user_id) or "ar"

    # حارس منع
    if not await guard_user(update, context, user_id, lang):
        return

    saved_lang = get_user_language(user_id)

    if not saved_lang:
        await update.message.reply_text(get_text("ar", "welcome"), reply_markup=get_language_keyboard())
        return

    # ✅ رسالة ترحيب مخصصة (جديد)
    welcome_msg = get_setting("welcome_message", "")
    if welcome_msg and not is_admin(user_id):
        try:
            await update.message.reply_text(welcome_msg)
        except:
            pass

    emails_count = len(get_user_emails(user_id))
    text = get_text(lang, "main_menu", emails_count=emails_count)
    await update.message.reply_text(text, reply_markup=get_main_menu_keyboard(lang, user_id))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(get_text("ar", "unauthorized"))
        return
    lang = get_user_language(user_id) or "ar"
    await update.message.reply_text("👑 لوحة المشرف", reply_markup=get_admin_panel_keyboard(lang, user_id))


# ================== الأزرار ==================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_active, bot_offline_message

    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    user_id = update.effective_user.id
    data = query.data
    lang = get_user_language(user_id) or "ar"

    # حارس منع (ينطبق على كل شيء لغير الأدمن)
    if not await guard_user(query, context, user_id, lang):
        return

    # اختيار اللغة
    if data.startswith("lang_"):
        chosen = data.split("_")[1]
        set_user_language(user_id, chosen, update.effective_user)

        emails_count = len(get_user_emails(user_id))
        text = get_text(chosen, "main_menu", emails_count=emails_count)
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(chosen, user_id))
        return

    # تغيير اللغة
    if data == "change_language":
        await query.edit_message_text(get_text("ar", "welcome"), reply_markup=get_language_keyboard())
        return

    # رجوع للقائمة
    if data == "back_to_menu":
        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang, user_id))
        return

    # إنشاء إيميل
    if data == "create_email":
        email, token = create_email()
        if email and token:
            add_user_email(user_id, email, token)
            await query.edit_message_text(get_text(lang, "email_created", email=email),
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]),
                                          parse_mode="HTML")
        else:
            await query.edit_message_text(get_text(lang, "error_create_email"),
                                          reply_markup=InlineKeyboardMarkup([
                                              [InlineKeyboardButton(get_text(lang, "btn_create"), callback_data="create_email")],
                                              [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]
                                          ]))
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
        email_index = int(data.split("_")[1])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        messages = check_inbox(email_data["token"])

        if messages is None:
            await query.edit_message_text(get_text(lang, "error_load_messages"),
                                          reply_markup=InlineKeyboardMarkup([
                                              [InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}")],
                                              [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")]
                                          ]))
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
        email_index = int(parts[1])
        msg_index = int(parts[2])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]

        messages = check_inbox(email_data["token"])
        if not messages or msg_index >= len(messages):
            return
        msg_id = messages[msg_index]["id"]

        full = get_message_content(msg_id, email_data["token"])
        if not full:
            await query.edit_message_text(get_text(lang, "error_load_message"),
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]))
            return

        sender = full.get("from", {}).get("address", "Unknown")
        subject = full.get("subject", "No Subject")
        date = full.get("createdAt", "Unknown")
        content = get_message_text(full)

        otp = extract_otp(content)
        content = content[:3500] + ("\n\n... (الرسالة طويلة جداً)" if lang=="ar" else "\n\n... (too long)") if len(content) > 3500 else content

        if otp:
            text = get_text(lang, "otp_found", otp=otp) + "\n\n" + get_text(lang, "message_detail",
                                                                           sender=sender, subject=subject, date=date, content=content)
        else:
            text = get_text(lang, "message_detail", sender=sender, subject=subject, date=date, content=content)

        await query.edit_message_text(text,
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]),
                                      parse_mode="HTML")
        return

    # عرض تفاصيل إيميل
    if data.startswith("view_email_"):
        email_index = int(data.split("_")[2])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        text = f"📧 <code>{email_data['address']}</code>\n🔑 <code>TempMail123</code>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_text(lang, "btn_inbox"), callback_data=f"inbox_{email_index}")],
            [InlineKeyboardButton(get_text(lang, "btn_delete"), callback_data=f"confirm_delete_{email_index}")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="my_emails")]
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    # تأكيد حذف إيميل
    if data.startswith("confirm_delete_") and data != "confirm_delete_all":
        email_index = int(data.split("_")[2])
        emails = get_user_emails(user_id)
        if email_index >= len(emails):
            return
        email_data = emails[email_index]
        text = f"⚠️ هل أنت متأكد من حذف هذا الإيميل؟\n\n📧 {email_data['address']}"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data=f"delete_{email_index}"),
            InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="my_emails")
        ]])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("delete_") and not data.startswith("delete_all"):
        email_index = int(data.split("_")[1])
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
            InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data="delete_all"),
            InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="back_to_menu")
        ]])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "delete_all":
        emails = get_user_emails(user_id)
        count = len(emails)
        user_database[str(user_id)]["emails"] = []
        save_single_user(str(user_id), user_database[str(user_id)])
        await query.edit_message_text(get_text(lang, "all_emails_deleted", count=count),
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        return

    # ✅ تحقق الاشتراك (زر)
    if data == "verify_subscription":
        ok = await check_user_subscription_strict(user_id, context)
        if ok:
            emails_count = len(get_user_emails(user_id))
            text = "✅ تم التحقق من اشتراكك بنجاح!\n\n" + get_text(lang, "main_menu", emails_count=emails_count) if lang=="ar" else \
                   "✅ Subscription verified!\n\n" + get_text(lang, "main_menu", emails_count=emails_count)
            await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang, user_id))
        else:
            ch = get_channel_info()
            if ch:
                msg = ch.get("subscription_message") or ""
                text, kb = subscription_prompt(lang, ch["channel_username"], msg)
                await query.edit_message_text(text, reply_markup=kb)
        return

    # ================== لوحة الأدمن (القديمة) ==================
    if data == "admin_panel":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return
        await query.edit_message_text("👑 لوحة تحكم المشرف\n\nاختر القسم:",
                                      reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    if data == "channel_management":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return

        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            status = "✅ مفعّل" if channel_info.get("subscription_enabled") else "❌ معطّل"
            msg = channel_info.get("subscription_message") or "لا توجد رسالة"
            cid = channel_info.get("channel_id", "غير محدد")
            title = channel_info.get("channel_title", "غير محدد")
            text = (
                "📢 معلومات القناة الحالية\n\n"
                f"القناة: @{channel_info['channel_username']}\n"
                f"الحالة: {status}\n"
                f"الرسالة: {msg}\n"
                f"📢 اسم القناة: <b>{title}</b>\n"
                f"🆔 معرّف القناة: <code>{cid}</code>"
            )
        else:
            text = "📢 إدارة قنوات الاشتراك الإجباري\n\nاختر الإجراء المطلوب:"

        await query.edit_message_text(text, reply_markup=get_channel_management_keyboard(lang), parse_mode="HTML")
        return

    if data == "set_channel":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "channel_username"
        await query.edit_message_text("📢 أرسل username القناة (بدون @)\nمثال: mychannel",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    if data == "set_channel_message":
        if not is_admin(user_id):
            return
        ch = get_channel_info(only_enabled=False)
        if not ch:
            await query.edit_message_text("❌ لا توجد قناة محددة", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
            return
        context.user_data["waiting_for"] = "channel_message"
        context.user_data["channel_username"] = ch["channel_username"]
        await query.edit_message_text("📝 أرسل رسالة الاشتراك الإجباري:",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    if data == "delete_channel":
        if not is_admin(user_id):
            return
        ch = get_channel_info(only_enabled=False)
        if ch:
            delete_channel(ch["channel_username"])
            await query.edit_message_text("✅ تم حذف القناة بنجاح",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        else:
            await query.edit_message_text("❌ لا توجد قناة", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    if data == "toggle_subscription":
        if not is_admin(user_id):
            return
        ch = get_channel_info(only_enabled=False)
        if ch:
            new_status = toggle_subscription(ch["channel_username"])
            action = "تفعيل" if new_status else "تعطيل"
            await query.edit_message_text(f"✅ تم {action} الاشتراك الإجباري",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    # أقسام الأدمن القديمة الأساسية (موجودة ومفعلة)
    if data == "section_stats":
        if not is_admin(user_id):
            return
        total_users = len(user_database)
        total_emails = sum(len(u.get("emails", [])) for u in user_database.values())
        active_users = sum(1 for u in user_database.values() if len(u.get("emails", [])) > 0)
        text = (
            "📊 قسم الإحصائيات\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"📧 إجمالي الإيميلات: {total_emails}\n"
            f"🔄 المستخدمون النشطون: {active_users}\n"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]))
        return

    if data == "section_forward":
        if not is_admin(user_id):
            return
        status = "✅ مفعّل" if forwarding_enabled else "❌ معطّل"
        text = f"📨 قسم توجيه الرسائل\n\nالحالة: {status}\n\nعند التفعيل، أي رسالة يرسلها المستخدمون ستصلك مباشرة."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تفعيل التوجيه", callback_data="forward_on")],
            [InlineKeyboardButton("❌ تعطيل التوجيه", callback_data="forward_off")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "forward_on":
        if not is_admin(user_id):
            return
        forwarding_enabled = True
        await query.edit_message_text("✅ تم تفعيل توجيه الرسائل!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]))
        return

    if data == "forward_off":
        if not is_admin(user_id):
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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔄 حالة البوت: {status_icon}", callback_data="toggle_bot_status")],
            [InlineKeyboardButton("✏️ رسالة الإيقاف", callback_data="set_offline_message")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 إذاعة للكل", callback_data="broadcast_all")],
            [InlineKeyboardButton("👥 إذاعة للنشطين فقط", callback_data="broadcast_active")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 قائمة كل الأعضاء", callback_data="users_list_all")],
            [InlineKeyboardButton("✅ الأعضاء النشطين", callback_data="users_list_active")],
            [InlineKeyboardButton("🏆 الأكثر إيميلات", callback_data="users_list_top")],
            [InlineKeyboardButton("🔍 بحث عن عضو", callback_data="search_member")],
            [InlineKeyboardButton("🗑 حذف عضو", callback_data="delete_member")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "users_list_all":
        if not is_admin(user_id):
            return
        text = "📋 قائمة كل الأعضاء\n━━━━━━━━━━━━━━━\n\n"
        count = 0
        for uid, info in list(user_database.items())[:20]:
            count += 1
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            status = "✅" if emails_count > 0 else "⚪"
            text += f"{count}. {status} <b>{name}</b>\n    🆔 {username} | 📧 {emails_count}\n    ID: <code>{uid}</code>\n\n"
        await query.edit_message_text(text, parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    if data == "users_list_active":
        if not is_admin(user_id):
            return
        active_members = [(uid, info) for uid, info in user_database.items() if len(info.get("emails", [])) > 0]
        text = f"✅ الأعضاء النشطين ({len(active_members)})\n━━━━━━━━━━━━━━━\n\n"
        count = 0
        for uid, info in active_members[:20]:
            count += 1
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            text += f"{count}. <b>{name}</b>\n    🆔 {username} | 📧 {emails_count}\n    ID: <code>{uid}</code>\n\n"
        await query.edit_message_text(text, parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    if data == "users_list_top":
        if not is_admin(user_id):
            return
        sorted_users = sorted(user_database.items(), key=lambda x: len(x[1].get("emails", [])), reverse=True)[:10]
        text = "🏆 الأكثر إيميلات\n━━━━━━━━━━━━━━━\n\n"
        medals = ["🥇", "🥈", "🥉"]
        rank = 0
        for uid, info in sorted_users:
            emails_count = len(info.get("emails", []))
            if emails_count == 0:
                continue
            rank += 1
            medal = medals[rank-1] if rank <= 3 else f"{rank}."
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            text += f"{medal} <b>{name}</b>\n    🆔 {username}\n    📧 {emails_count}\n    ID: <code>{uid}</code>\n\n"
        await query.edit_message_text(text, parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    if data == "search_member":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "search_member"
        await query.edit_message_text("🔍 أرسل ID أو username أو اسم للبحث:",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    if data == "delete_member":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "delete_member"
        await query.edit_message_text("🗑 أرسل ID المستخدم لحذفه نهائياً من قاعدة البيانات (bot_users):\nمثال: 123456789",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
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
                text += f"• {name} | {username}\n  ID: <code>{a['telegram_id']}</code>\n"
        else:
            text += "لا يوجد مشرفون إضافيون حالياً\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة مشرف", callback_data="add_admin")],
            [InlineKeyboardButton("➖ إزالة مشرف", callback_data="remove_admin")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
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
        kb_rows = []
        for a in admins:
            name = a.get("first_name") or str(a["telegram_id"])
            kb_rows.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"confirm_remove_admin_{a['telegram_id']}")])
        kb_rows.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")])
        await query.edit_message_text("➖ اختر المشرف لإزالته:", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("confirm_remove_admin_"):
        if user_id != ADMIN_ID:
            return
        aid = int(data.replace("confirm_remove_admin_", ""))
        ok = remove_admin(aid)
        await query.edit_message_text("✅ تم إزالة المشرف" if ok else "❌ فشل إزالة المشرف",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]))
        return

    # ================== ✅ ميزاتك الجديدة (حظر/ترحيب) ==================
    if data == "section_ban":
        if not is_admin(user_id):
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 حظر مستخدم", callback_data="ban_user")],
            [InlineKeyboardButton("✅ فك حظر مستخدم", callback_data="unban_user")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
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
        current = get_setting("welcome_message", "")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ تعيين رسالة الترحيب", callback_data="set_welcome_message")],
            [InlineKeyboardButton("🧹 حذف رسالة الترحيب", callback_data="clear_welcome_message")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ])
        text = "👋 رسالة الترحيب الحالية:\n\n"
        text += (current if current else "— لا توجد رسالة —")
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "set_welcome_message":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "welcome_message"
        await query.edit_message_text("✏️ أرسل رسالة الترحيب الجديدة التي ستظهر عند /start (لغير المشرفين):",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]))
        return

    if data == "clear_welcome_message":
        if not is_admin(user_id):
            return
        set_setting("welcome_message", "")
        await query.edit_message_text("✅ تم حذف رسالة الترحيب",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]))
        return

    if data == "bot_info":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return
        text = "ℹ️ معلومات البوت\n\n🤖 Name: Temp Email Bot\n📌 Version: 2.1\n📧 API: mail.tm\n✅ Added: Strict Sub + Welcome + Ban"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]))
        return


# ================== معالج الرسائل النصية (مثل كودك + إضافات انتظار الإدخال) ==================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_offline_message

    user_id = update.effective_user.id
    user = update.effective_user
    update_user_info(user_id, user)

    lang = get_user_language(user_id) or "ar"

    # حارس منع
    if not await guard_user(update, context, user_id, lang):
        return

    # توجيه رسائل المستخدمين للأدمن إذا مفعّل
    if forwarding_enabled and user_id != ADMIN_ID:
        try:
            user_name = user.first_name or ""
            if user.last_name:
                user_name += f" {user.last_name}"
            username = f"@{user.username}" if user.username else "لا يوجد"
            forward_text = (
                "📨 <b>رسالة جديدة من مستخدم:</b>\n\n"
                f"👤 الاسم: {user_name}\n"
                f"🆔 المعرف: {username}\n"
                f"🔢 ID: <code>{user_id}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💬 الرسالة:\n{update.message.text}"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode="HTML")
        except Exception as e:
            print(f"❌ فشل توجيه الرسالة للأدمن: {e}")

    waiting_for = context.user_data.get("waiting_for")
    if not waiting_for:
        return

    # تعيين قناة
    if waiting_for == "channel_username" and is_admin(user_id):
        channel_username = (update.message.text or "").strip().replace("@", "")
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            ok = set_channel(channel_username, chat.id, chat.title)
            text = f"✅ تم تعيين القناة @{channel_username}\n🆔 {chat.id}\n📢 {chat.title}" if ok else "❌ فشل تعيين القناة"
        except Exception as e:
            text = f"❌ خطأ: {str(e)[:200]}"
        context.user_data["waiting_for"] = None
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    # تعيين رسالة القناة
    if waiting_for == "channel_message" and is_admin(user_id):
        msg = update.message.text or ""
        ch = context.user_data.get("channel_username")
        ok = bool(ch) and set_channel_message(ch, msg)
        context.user_data["waiting_for"] = None
        context.user_data["channel_username"] = None
        await update.message.reply_text("✅ تم حفظ الرسالة" if ok else "❌ فشل حفظ الرسالة",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    # رسالة الإيقاف
    if waiting_for == "offline_message" and is_admin(user_id):
        bot_offline_message = (update.message.text or "").strip()
        context.user_data["waiting_for"] = None
        await update.message.reply_text("✅ تم حفظ رسالة الإيقاف",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]))
        return

    # إذاعة للكل
    if waiting_for == "broadcast_all" and is_admin(user_id):
        context.user_data["waiting_for"] = None
        msg = update.message.text or ""
        wait_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة...")
        okc = 0
        fail = 0
        for uid in list(user_database.keys()):
            try:
                await context.bot.send_message(chat_id=int(uid), text=f"📢 رسالة من الإدارة:\n\n{msg}")
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
        wait_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة للنشطين...")
        okc = 0
        fail = 0
        for uid, info in user_database.items():
            if len(info.get("emails", [])) > 0:
                try:
                    await context.bot.send_message(chat_id=int(uid), text=f"📢 رسالة من الإدارة:\n\n{msg}")
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

        text = f"🔍 نتائج البحث عن '{q}':\n━━━━━━━━━━━━━━━\n\n"
        for uid, info in results[:10]:
            name = (info.get("first_name") or "مجهول") + (f" {info.get('last_name')}" if info.get("last_name") else "")
            username = f"@{info.get('username')}" if info.get("username") else "—"
            emails_count = len(info.get("emails", []))
            status = "✅ نشط" if emails_count > 0 else "⚪ غير نشط"
            text += f"👤 <b>{name}</b>\n🆔 {username}\n📧 {emails_count} | {status}\n🔢 ID: <code>{uid}</code>\n\n"

        await update.message.reply_text(text, parse_mode="HTML",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return

    # 🗑 حذف عضو (جديد) - من قسم الأدمن فقط
    if waiting_for == "delete_member" and is_admin(user_id):
        raw = (update.message.text or "").strip()
        context.user_data["waiting_for"] = None
        try:
            target_id = int(raw)
        except Exception:
            await update.message.reply_text("❌ ارسل ID صحيح (أرقام فقط)",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
            return

        # منع حذف أي مشرف (حماية)
        if is_admin(target_id):
            await update.message.reply_text("⚠️ لا يمكن حذف مشرف من هنا.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
            return

        ok = delete_member_from_bot_users(target_id)
        if ok:
            try:
                user_database.pop(str(target_id), None)
            except Exception:
                pass
            await update.message.reply_text("✅ تم حذف المستخدم نهائياً من قاعدة البيانات",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        else:
            await update.message.reply_text("❌ المستخدم غير موجود أو فشل الحذف",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]))
        return


    # ✅ تعيين رسالة الترحيب (جديد)
    if waiting_for == "welcome_message" and is_admin(user_id):
        msg = (update.message.text or "").strip()
        set_setting("welcome_message", msg)
        context.user_data["waiting_for"] = None
        await update.message.reply_text("✅ تم حفظ رسالة الترحيب",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]))
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


# ================== Error Handler ==================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = str(context.error)
    ignorable = ["Query is too old", "query id is invalid", "Message is not modified"]
    if any(x in err for x in ignorable):
        return
    print(f"❌ ERROR: {context.error}")


# ================== تشغيل ==================

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ ضع TELEGRAM_BOT_TOKEN بمتغيرات البيئة")
        return

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    application.add_error_handler(error_handler)

    # ✅ جديد: فحص تلقائي لصندوق الوارد (mail.tm) وإرسال الرسائل فور وصولها
    try:
        if application.job_queue:
            application.job_queue.run_repeating(poll_inboxes_job, interval=20, first=10, name="poll_inboxes")
            print("✅ Auto inbox polling enabled (every 20s)")
        else:
            print("⚠️ JobQueue غير متاح - لن يعمل الفحص التلقائي")
    except Exception as e:
        print(f"⚠️ Failed to start polling job: {e}")

    print("🤖 Bot is running (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
