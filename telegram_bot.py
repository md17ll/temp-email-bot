#!/usr/bin/env python3
"""
بوت تلجرام لإنشاء إيميلات مؤقتة متعددة
Telegram Temp Email Bot with Multiple Emails Support

تحديثات:
- اشتراك إجباري قوي: فحص عند /start + كل Callback + كل رسالة
- رسالة ترحيب قابلة للتعيين من الأدمن
- حظر مستخدم / فك حظر (مع تخزين في PostgreSQL)
"""

import requests
import re
import os
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# إعدادات البوت
API = "https://api.mail.tm"
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "6436207302"))

# حالة توجيه الرسائل للأدمن
forwarding_enabled = False

# حالة تشغيل البوت
bot_active = True
bot_offline_message = ""

DATABASE_URL = os.getenv("DATABASE_URL")

# ======= كاش بسيط للاشتراك الإجباري لتخفيف ضغط API (مع بقاءه "صارم") =======
SUB_CHECK_TTL_SECONDS = 30  # كل 30 ثانية يعيد التحقق (حتى لو خرجو)
_sub_cache = {}  # user_id -> {"ok": bool, "ts": datetime}

# ============= إدارة قاعدة البيانات =============

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
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

            # banned users
            cur.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    telegram_id BIGINT PRIMARY KEY,
                    reason TEXT,
                    banned_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # bot settings (welcome message, ...etc)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
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
                user_id = str(row['telegram_id'])
                user_data[user_id] = {
                    'lang': row['language'],
                    'first_name': row.get('first_name', ''),
                    'last_name': row.get('last_name', ''),
                    'username': row.get('username', ''),
                    'emails': row['emails'] or []
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
                user_info.get('lang'),
                user_info.get('first_name', ''),
                user_info.get('last_name', ''),
                user_info.get('username', ''),
                Json(user_info.get('emails', []))
            ))
            conn.commit()
    except Exception as e:
        print(f"❌ خطأ في حفظ البيانات: {e}")
        conn.rollback()
    finally:
        conn.close()

# ============= إعدادات البوت =============

def set_setting(key: str, value: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """, (key, value))
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ خطأ في حفظ الإعداد {key}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

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
        print(f"❌ خطأ في قراءة الإعداد {key}: {e}")
        return default
    finally:
        conn.close()

WELCOME_KEY = "welcome_message"

# ============= الحظر =============

def is_banned(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return False
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM banned_users WHERE telegram_id=%s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        print(f"❌ خطأ في فحص الحظر: {e}")
        return False
    finally:
        conn.close()

def ban_user(user_id: int, banned_by: int, reason: str = "") -> bool:
    if user_id == ADMIN_ID:
        return False
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO banned_users (telegram_id, reason, banned_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET reason = EXCLUDED.reason, banned_by = EXCLUDED.banned_by
            """, (user_id, reason, banned_by))
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ خطأ في حظر المستخدم: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def unban_user(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return False
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM banned_users WHERE telegram_id=%s", (user_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"❌ خطأ في فك الحظر: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# ============= وظائف إدارة المشرفين =============

def is_admin(user_id):
    if user_id == ADMIN_ID:
        return True
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admins WHERE telegram_id = %s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        print(f"❌ خطأ في التحقق من المشرف: {e}")
        return False
    finally:
        conn.close()

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
            cur.execute("DELETE FROM admins WHERE telegram_id = %s", (telegram_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"❌ خطأ في إزالة المشرف: {e}")
        return False
    finally:
        conn.close()

def find_user_by_username_or_id(search_query, user_database):
    search_query = str(search_query).strip().lstrip('@')

    for uid, info in user_database.items():
        if uid == search_query:
            return {'telegram_id': int(uid), 'username': info.get('username'), 'first_name': info.get('first_name')}
        if info.get('username', '').lower() == search_query.lower():
            return {'telegram_id': int(uid), 'username': info.get('username'), 'first_name': info.get('first_name')}

    try:
        user_id = int(search_query)
        if str(user_id) in user_database:
            info = user_database[str(user_id)]
            return {'telegram_id': user_id, 'username': info.get('username'), 'first_name': info.get('first_name')}
        return {'telegram_id': user_id, 'username': None, 'first_name': None}
    except ValueError:
        pass

    return None

# ============= وظائف إدارة القنوات =============

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
            cur.execute("SELECT id FROM channels WHERE channel_username = %s", (channel_username,))
            if not cur.fetchone():
                return False

            cur.execute("""
                UPDATE channels
                SET subscription_message = %s, updated_at = CURRENT_TIMESTAMP
                WHERE channel_username = %s
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
            cur.execute("DELETE FROM channels WHERE channel_username = %s", (channel_username,))
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
                SET subscription_enabled = NOT subscription_enabled, updated_at = CURRENT_TIMESTAMP
                WHERE channel_username = %s
                RETURNING subscription_enabled
            """, (channel_username,))
            result = cur.fetchone()
            conn.commit()
            return result[0] if result else False
    except Exception as e:
        print(f"❌ خطأ في تبديل حالة الاشتراك: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# ======== اشتراك إجباري "صارم" ========

async def check_user_subscription_strict(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    صارم: إذا ما قدر يتحقق لأي سبب (غير عدم وجود قناة) => يمنع.
    """
    # الأدمن يتجاوز
    if is_admin(user_id):
        return True

    channel_info = get_channel_info()
    if not channel_info:
        return True
    if not channel_info.get('subscription_enabled'):
        return True

    # كاش
    now = datetime.utcnow()
    cached = _sub_cache.get(user_id)
    if cached:
        if (now - cached["ts"]).total_seconds() <= SUB_CHECK_TTL_SECONDS:
            return cached["ok"]

    channel_id = channel_info.get('channel_id')
    channel_username = channel_info['channel_username']
    chat_identifier = channel_id if channel_id else f"@{channel_username}"

    try:
        member = await context.bot.get_chat_member(chat_identifier, user_id)
        ok = member.status in ['member', 'administrator', 'creator']
        _sub_cache[user_id] = {"ok": ok, "ts": now}
        return ok
    except Exception as e:
        # صارم: أي خطأ = منع (حتى ما يفلت اللي طلع من القناة)
        print(f"⚠️ strict sub check error for {user_id}: {e}")
        _sub_cache[user_id] = {"ok": False, "ts": now}
        return False

def subscription_block_message(lang: str):
    channel_info = get_channel_info()
    if not channel_info:
        return None, None

    message = channel_info.get('subscription_message') or ""
    text_ar = (
        "⚠️ يجب عليك الاشتراك في القناة للاستخدام\n\n"
        f"🔗 القناة: @{channel_info['channel_username']}\n\n"
        f"{message}\n\n"
        "بعد الاشتراك، اضغط على زر '✅ التحقق من الاشتراك'"
    )
    text_en = (
        "⚠️ You must join the channel to use the bot\n\n"
        f"🔗 Channel: @{channel_info['channel_username']}\n\n"
        f"{message}\n\n"
        "After joining, press '✅ Verify Subscription'"
    )
    text = text_ar if lang == "ar" else text_en

    keyboard = [
        [InlineKeyboardButton("📢 الانضمام للقناة" if lang == "ar" else "📢 Join Channel",
                              url=f"https://t.me/{channel_info['channel_username']}")],
        [InlineKeyboardButton("✅ التحقق من الاشتراك" if lang == "ar" else "✅ Verify Subscription",
                              callback_data="verify_subscription")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# ============= النصوص متعددة اللغات =============

def get_text(lang, key, **kwargs):
    texts = {
        "ar": {
            "welcome": "🎉 مرحباً بك في بوت الإيميلات المؤقتة!\n\nاختر لغتك المفضلة:",
            "main_menu": "📬 القائمة الرئيسية\n\nعدد الإيميلات النشطة: {emails_count}",
            "email_created": "✅ تم إنشاء بريد إلكتروني جديد!\n\n📧 الإيميل: <code>{email}</code>\n\nاضغط على الإيميل للنسخ",
            "no_emails": "❌ لا توجد إيميلات نشطة\n\nقم بإنشاء إيميل جديد أولاً",
            "select_email": "📋 اختر الإيميل لعرض الرسائل:\n\nعدد الإيميلات: {count}",
            "no_messages": "📭 لا توجد رسائل في هذا الإيميل\n\n📧 {email}",
            "messages_list": "📬 الرسائل الواردة ({count})\n📧 الإيميل: {email}\n\n",
            "message_detail": "✉️ تفاصيل الرسالة\n\n📧 من: {sender}\n📌 الموضوع: {subject}\n📅 التاريخ: {date}\n\n📝 المحتوى:\n{content}\n",
            "otp_found": "🔢 تم العثور على رمز OTP:\n\nالرمز: <code>{otp}</code>\n\nاضغط على الرمز للنسخ",
            "email_deleted": "🗑️ تم حذف الإيميل بنجاح\n\n📧 {email}",
            "all_emails_deleted": "🗑️ تم حذف جميع الإيميلات ({count})",
            "confirm_delete": "⚠️ هل أنت متأكد من حذف هذا الإيميل؟\n\n📧 {email}",
            "confirm_delete_all": "⚠️ هل أنت متأكد من حذف جميع الإيميلات؟\n\nالعدد: {count}",
            "stats": "📊 الإحصائيات\n\n👤 المستخدمين الكليين: {total_users}\n📧 إيميلاتك النشطة: {user_emails}\n📬 إجمالي الرسائل: {total_messages}\n🌐 اللغة: العربية",
            "admin_stats": "👑 إحصائيات المشرف\n\n👥 إجمالي المستخدمين: {total_users}\n📧 إجمالي الإيميلات: {total_emails}\n📬 إجمالي الرسائل: {total_messages}\n🔄 المستخدمون النشطون: {active_users}",
            "language_changed": "✅ تم تغيير اللغة إلى العربية",
            "error": "❌ حدث خطأ، حاول مرة أخرى",
            "error_create_email": "❌ فشل إنشاء الإيميل\n\nقد تكون الخدمة مشغولة حالياً.\nالرجاء المحاولة مرة أخرى.",
            "error_load_messages": "❌ فشل تحميل الرسائل\n\nقد يكون الاتصال بالخدمة بطيئاً.\nاضغط 🔄 تحديث للمحاولة مرة أخرى.",
            "error_load_message": "❌ فشل تحميل الرسالة\n\nحاول مرة أخرى لاحقاً.",
            "unauthorized": "⛔ عذراً، هذا الأمر متاح للمشرف فقط",
            "banned": "⛔ تم حظرك من استخدام البوت.",
            # أزرار
            "btn_create": "✨ إنشاء إيميل جديد",
            "btn_my_emails": "📧 إيميلاتي",
            "btn_inbox": "📥 الرسائل الواردة",
            "btn_stats": "📊 الإحصائيات",
            "btn_delete_all": "🗑️ حذف الكل",
            "btn_language": "🌐 تغيير اللغة",
            "btn_back": "🔙 رجوع",
            "btn_delete": "🗑️ حذف",
            "btn_confirm": "✅ تأكيد",
            "btn_cancel": "❌ إلغاء",
            "btn_refresh": "🔄 تحديث",
            "btn_admin_panel": "👑 لوحة المشرف",
            # لوحة المشرف
            "admin_panel": "👑 لوحة تحكم المشرف\n\nمرحباً بك في لوحة التحكم",
        },
        "en": {
            "welcome": "🎉 Welcome to Temp Email Bot!\n\nChoose your preferred language:",
            "main_menu": "📬 Main Menu\n\nActive emails: {emails_count}",
            "email_created": "✅ New email created successfully!\n\n📧 Email: <code>{email}</code>\n\nTap to copy",
            "no_emails": "❌ No active emails\n\nCreate a new email first",
            "select_email": "📋 Select email to view messages:\n\nTotal emails: {count}",
            "no_messages": "📭 No messages in this email\n\n📧 {email}",
            "messages_list": "📬 Inbox ({count})\n📧 Email: {email}\n\n",
            "message_detail": "✉️ Message Details\n\n📧 From: {sender}\n📌 Subject: {subject}\n📅 Date: {date}\n\n📝 Content:\n{content}\n",
            "otp_found": "🔢 OTP Code Found:\n\nCode: <code>{otp}</code>\n\nTap to copy",
            "email_deleted": "🗑️ Email deleted successfully\n\n📧 {email}",
            "all_emails_deleted": "🗑️ All emails deleted ({count})",
            "confirm_delete": "⚠️ Are you sure you want to delete this email?\n\n📧 {email}",
            "confirm_delete_all": "⚠️ Are you sure you want to delete all emails?\n\nCount: {count}",
            "stats": "📊 Statistics\n\n👤 Total Users: {total_users}\n📧 Your Active Emails: {user_emails}\n📬 Total Messages: {total_messages}\n🌐 Language: English",
            "admin_stats": "👑 Admin Statistics\n\n👥 Total Users: {total_users}\n📧 Total Emails: {total_emails}\n📬 Total Messages: {total_messages}\n🔄 Active Users: {active_users}",
            "language_changed": "✅ Language changed to English",
            "error": "❌ An error occurred, please try again",
            "error_create_email": "❌ Failed to create email\n\nThe service may be busy.\nPlease try again.",
            "error_load_messages": "❌ Failed to load messages\n\nConnection may be slow.\nPress 🔄 Refresh to try again.",
            "error_load_message": "❌ Failed to load message\n\nPlease try again later.",
            "unauthorized": "⛔ Sorry, this command is for admin only",
            "banned": "⛔ You are banned from using this bot.",
            # Buttons
            "btn_create": "✨ Create New Email",
            "btn_my_emails": "📧 My Emails",
            "btn_inbox": "📥 Inbox",
            "btn_stats": "📊 Statistics",
            "btn_delete_all": "🗑️ Delete All",
            "btn_language": "🌐 Change Language",
            "btn_back": "🔙 Back",
            "btn_delete": "🗑️ Delete",
            "btn_confirm": "✅ Confirm",
            "btn_cancel": "❌ Cancel",
            "btn_refresh": "🔄 Refresh",
            "btn_admin_panel": "👑 Admin Panel",
            # Admin Panel
            "admin_panel": "👑 Admin Control Panel\n\nWelcome to the control panel",
        }
    }
    text = texts.get(lang, texts["ar"]).get(key, "")
    return text.format(**kwargs) if kwargs else text

# ============= وظائف API =============

def get_available_domains():
    try:
        response = requests.get(f"{API}/domains", timeout=10)
        if response.status_code == 200:
            data = response.json()
            domains = data.get('hydra:member', [])
            return [d['domain'] for d in domains] if domains else []
    except Exception as e:
        print(f"⚠️ خطأ في الحصول على النطاقات: {e}")
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

        response = requests.post(f"{API}/accounts", json={"address": email_address, "password": password}, timeout=10)
        if response.status_code == 201:
            token_response = requests.post(f"{API}/token", json={"address": email_address, "password": password}, timeout=10)
            if token_response.status_code == 200:
                token_data = token_response.json()
                token = token_data.get('token')
                if token:
                    return email_address, token
    except Exception as e:
        print(f"❌ خطأ في إنشاء الإيميل: {e}")

    return None, None

def check_inbox(token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{API}/messages", headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('hydra:member', [])
        return None
    except Exception as e:
        print(f"⚠️ خطأ في فحص الصندوق: {e}")
        return None

def get_message_content(message_id, token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{API}/messages/{message_id}", headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"⚠️ خطأ في الحصول على الرسالة: {e}")
    return None

def extract_otp(text):
    if not text:
        return None
    match = re.search(r'\b(\d{4,8})\b', text)
    return match.group(1) if match else None

# ============= بيانات المستخدمين =============

init_database()
user_database = load_user_data()

def get_user_data(user_id):
    user_id_str = str(user_id)
    if user_id_str not in user_database:
        user_database[user_id_str] = {'lang': None, 'emails': []}
        save_single_user(user_id_str, user_database[user_id_str])
    return user_database[user_id_str]

def get_user_language(user_id):
    return get_user_data(user_id).get('lang')

def set_user_language(user_id, lang, user_info=None):
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['lang'] = lang
    if user_info:
        user_data['first_name'] = user_info.first_name or ''
        user_data['last_name'] = user_info.last_name or ''
        user_data['username'] = user_info.username or ''
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def update_user_info(user_id, user_info):
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['first_name'] = user_info.first_name or ''
    user_data['last_name'] = user_info.last_name or ''
    user_data['username'] = user_info.username or ''
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def add_user_email(user_id, email, token):
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['emails'].append({'address': email, 'token': token})
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def remove_user_email(user_id, email):
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['emails'] = [e for e in user_data['emails'] if e['address'] != email]
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def get_user_emails(user_id):
    return get_user_data(user_id).get('emails', [])

# ============= لوحات المفاتيح =============

def get_language_keyboard():
    keyboard = [[
        InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    ]]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu_keyboard(lang, user_id):
    keyboard = [
        [InlineKeyboardButton(get_text(lang, "btn_create"), callback_data="create_email")],
        [
            InlineKeyboardButton(get_text(lang, "btn_my_emails"), callback_data="my_emails"),
            InlineKeyboardButton(get_text(lang, "btn_inbox"), callback_data="select_inbox")
        ],
        [InlineKeyboardButton(get_text(lang, "btn_delete_all"), callback_data="confirm_delete_all")],
        [InlineKeyboardButton(get_text(lang, "btn_language"), callback_data="change_language")]
    ]

    if is_admin(user_id):
        keyboard.insert(2, [InlineKeyboardButton(get_text(lang, "btn_stats"), callback_data="stats")])
        keyboard.insert(3, [InlineKeyboardButton(get_text(lang, "btn_admin_panel"), callback_data="admin_panel")])

    return InlineKeyboardMarkup(keyboard)

def get_email_list_keyboard(emails, action_prefix, lang):
    keyboard = []
    for i, email_data in enumerate(emails):
        email = email_data['address']
        display_email = email if len(email) <= 30 else email[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"📧 {display_email}", callback_data=f"{action_prefix}_{i}")])
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_messages_keyboard(messages, email_index, lang):
    keyboard = []
    for i, msg in enumerate(messages[:10]):
        subject = msg.get('subject', 'No Subject')
        display_subject = subject if len(subject) <= 30 else subject[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"✉️ {display_subject}", callback_data=f"msg_{email_index}_{i}")])
    keyboard.append([
        InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}"),
        InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel_keyboard(lang, user_id):
    keyboard = [
        [InlineKeyboardButton("📢 إدارة القنوات" if lang == "ar" else "📢 Channel Management", callback_data="channel_management")],
        [InlineKeyboardButton("✍️ رسالة الترحيب" if lang == "ar" else "✍️ Welcome Message", callback_data="set_welcome_message")],
        [InlineKeyboardButton("🚫 حظر مستخدم" if lang == "ar" else "🚫 Ban User", callback_data="ban_user")],
        [InlineKeyboardButton("✅ فك حظر" if lang == "ar" else "✅ Unban User", callback_data="unban_user")],
        [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_channel_management_keyboard(lang):
    channel_info = get_channel_info(only_enabled=False)
    keyboard = [
        [InlineKeyboardButton("تعيين القناة" if lang == "ar" else "Set Channel", callback_data="set_channel")],
        [InlineKeyboardButton("تعيين رسالة الاشتراك" if lang == "ar" else "Set Subscription Message", callback_data="set_channel_message")],
    ]
    if channel_info:
        status_text = "✅" if channel_info.get('subscription_enabled') else "❌"
        keyboard.append([InlineKeyboardButton(
            ("إشعار الاشتراك: " if lang == "ar" else "Subscription: ") + status_text,
            callback_data="toggle_subscription"
        )])
        keyboard.append([InlineKeyboardButton("حذف القناة" if lang == "ar" else "Delete Channel", callback_data="delete_channel")])

    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

# ============= حماية موحّدة (حظر + اشتراك + إيقاف) =============

async def enforce_access_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    user_id = update.effective_user.id

    # banned
    if is_banned(user_id) and not is_admin(user_id):
        msg = get_text(lang, "banned")
        if update.message:
            await update.message.reply_text(msg)
        else:
            try:
                await update.callback_query.edit_message_text(msg)
            except Exception:
                pass
        return False

    # bot active
    if not bot_active and not is_admin(user_id):
        text = f"⚠️ البوت متوقف مؤقتاً\n\n{bot_offline_message}" if bot_offline_message else "⚠️ البوت متوقف مؤقتاً. يرجى المحاولة لاحقاً."
        if update.message:
            await update.message.reply_text(text)
        else:
            try:
                await update.callback_query.edit_message_text(text)
            except Exception:
                pass
        return False

    # subscription strict
    ok = await check_user_subscription_strict(user_id, context)
    if not ok:
        text, kb = subscription_block_message(lang)
        if update.message:
            await update.message.reply_text(text, reply_markup=kb)
        else:
            try:
                await update.callback_query.edit_message_text(text, reply_markup=kb)
            except Exception:
                pass
        return False

    return True

# ============= أوامر =============

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user

    # تحديث معلومات المستخدم دائماً
    update_user_info(user_id, user)

    saved_lang = get_user_language(user_id)

    # لو محظور/موقوف -> رسالة مباشرة
    user_lang = saved_lang or "ar"
    # enforce (حتى /start صار صارم)
    if not await enforce_access_or_reply(update, context, user_lang if saved_lang else "ar"):
        return

    # رسالة ترحيب (قابلة للتعيين من الأدمن)
    custom_welcome = get_setting(WELCOME_KEY, "").strip()
    if custom_welcome:
        try:
            await update.message.reply_text(custom_welcome)
        except Exception:
            pass

    if saved_lang:
        emails_count = len(get_user_emails(user_id))
        text = get_text(user_lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(user_lang, user_id)
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
        # اختيار اللغة لأول مرة
        text = get_text("ar", "welcome")
        keyboard = get_language_keyboard()
        await update.message.reply_text(text, reply_markup=keyboard)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(get_text("ar", "unauthorized"))
        return

    lang = get_user_language(user_id) or "ar"
    total_users = len(user_database)
    total_emails = sum(len(user.get('emails', [])) for user in user_database.values())
    active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)

    text = get_text(lang, "admin_stats",
                    total_users=total_users,
                    total_emails=total_emails,
                    total_messages=0,
                    active_users=active_users)
    keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ============= الأزرار =============

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_active, bot_offline_message

    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = update.effective_user.id
    data = query.data

    # لغة
    if data.startswith("lang_"):
        lang = data.split("_")[1]
        user = update.effective_user
        set_user_language(user_id, lang, user)

        # بعد اختيار اللغة لازم كمان نعمل enforce اشتراك صارم
        if not await enforce_access_or_reply(update, context, lang):
            return

        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    lang = get_user_language(user_id) or "ar"

    # السماح بأزرار الاشتراك/الانضمام حتى لو مو مشترك
    allowed_without_sub = {"verify_subscription", "change_language"}
    if data not in allowed_without_sub:
        if not await enforce_access_or_reply(update, context, lang):
            return

    if data == "change_language":
        await query.edit_message_text(get_text("ar", "welcome"), reply_markup=get_language_keyboard())
        return

    if data == "back_to_menu":
        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    # verify subscription
    if data == "verify_subscription":
        ok = await check_user_subscription_strict(user_id, context)
        if ok:
            emails_count = len(get_user_emails(user_id))
            text = ("✅ تم التحقق من اشتراكك بنجاح!\n\n" if lang == "ar" else "✅ Subscription verified!\n\n")
            text += get_text(lang, "main_menu", emails_count=emails_count)
            keyboard = get_main_menu_keyboard(lang, user_id)
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            text, kb = subscription_block_message(lang)
            await query.edit_message_text(text, reply_markup=kb)
        return

    # ===== Admin Panel =====
    if data == "admin_panel":
        if not is_admin(user_id):
            return
        text = get_text(lang, "admin_panel")
        await query.edit_message_text(text, reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    # set welcome message
    if data == "set_welcome_message":
        if not is_admin(user_id):
            return
        context.user_data['waiting_for'] = 'welcome_message'
        current = get_setting(WELCOME_KEY, "").strip()
        text = "✍️ أرسل رسالة الترحيب الجديدة التي ستظهر عند /start.\n\n" if lang == "ar" else "✍️ Send the new welcome message shown on /start.\n\n"
        if current:
            text += f"📝 الرسالة الحالية:\n{current}\n\n"
        text += ("(أرسل كلمة حذف لإزالة الرسالة)" if lang == "ar" else "(Send 'delete' to remove it)")
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]))
        return

    # ban user
    if data == "ban_user":
        if not is_admin(user_id):
            return
        context.user_data['waiting_for'] = 'ban_user'
        text = "🚫 أرسل ID المستخدم أو @username لحظره.\n" if lang == "ar" else "🚫 Send user ID or @username to ban.\n"
        text += "يمكنك إضافة سبب بعده مثل: 12345 سبام" if lang == "ar" else "You can add reason after it: 12345 spam"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]))
        return

    # unban user
    if data == "unban_user":
        if not is_admin(user_id):
            return
        context.user_data['waiting_for'] = 'unban_user'
        text = "✅ أرسل ID المستخدم أو @username لفك الحظر.\n" if lang == "ar" else "✅ Send user ID or @username to unban.\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]))
        return

    # channel management
    if data == "channel_management":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            status = "✅ مفعّل" if channel_info.get('subscription_enabled') else "❌ معطّل"
            msg = channel_info.get('subscription_message') or "لا توجد رسالة"
            title = channel_info.get('channel_title') or "غير محدد"
            cid = channel_info.get('channel_id') or "غير محدد"
            text = f"📢 القناة الحالية: @{channel_info['channel_username']}\nالحالة: {status}\n\n📝 الرسالة:\n{msg}\n\n📌 اسم القناة: {title}\n🆔 ID: {cid}"
        else:
            text = "📢 إدارة قنوات الاشتراك الإجباري" if lang == "ar" else "📢 Channel Management"
        await query.edit_message_text(text, reply_markup=get_channel_management_keyboard(lang))
        return

    if data == "set_channel":
        if not is_admin(user_id):
            return
        context.user_data['waiting_for'] = 'channel_username'
        text = "📢 أرسل username القناة بدون @\nمثال: mychannel" if lang == "ar" else "📢 Send channel username without @\nExample: mychannel"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    if data == "set_channel_message":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if not channel_info:
            await query.edit_message_text("❌ لا توجد قناة محددة" if lang == "ar" else "❌ No channel set",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
            return
        context.user_data['waiting_for'] = 'channel_message'
        context.user_data['channel_username'] = channel_info['channel_username']
        text = "📝 أرسل رسالة الاشتراك التي ستظهر للمستخدمين:" if lang == "ar" else "📝 Send subscription message shown to users:"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    if data == "toggle_subscription":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            new_status = toggle_subscription(channel_info['channel_username'])
            text = "✅ تم تفعيل الاشتراك الإجباري" if new_status else "✅ تم تعطيل الاشتراك الإجباري"
        else:
            text = "❌ لا توجد قناة" if lang == "ar" else "❌ No channel"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    if data == "delete_channel":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            delete_channel(channel_info['channel_username'])
            text = "✅ تم حذف القناة" if lang == "ar" else "✅ Channel deleted"
        else:
            text = "❌ لا توجد قناة" if lang == "ar" else "❌ No channel"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return

    # ===== core bot actions (no change) =====
    if data == "create_email":
        email, token = create_email()
        if email and token:
            add_user_email(user_id, email, token)
            text = get_text(lang, "email_created", email=email)
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else:
            text = get_text(lang, "error_create_email")
            keyboard = [
                [InlineKeyboardButton(get_text(lang, "btn_create"), callback_data="create_email")],
                [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "my_emails":
        emails = get_user_emails(user_id)
        if not emails:
            text = get_text(lang, "no_emails")
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        else:
            text = get_text(lang, "select_email", count=len(emails))
            await query.edit_message_text(text, reply_markup=get_email_list_keyboard(emails, "view_email", lang))
        return

    if data == "select_inbox":
        emails = get_user_emails(user_id)
        if not emails:
            text = get_text(lang, "no_emails")
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        else:
            text = get_text(lang, "select_email", count=len(emails))
            await query.edit_message_text(text, reply_markup=get_email_list_keyboard(emails, "inbox", lang))
        return

    if data.startswith("inbox_"):
        email_index = int(data.split("_")[1])
        emails = get_user_emails(user_id)
        if email_index < len(emails):
            email_data = emails[email_index]
            messages = check_inbox(email_data['token'])

            if messages is None:
                text = get_text(lang, "error_load_messages")
                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}")],
                    [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")]
                ]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            elif len(messages) == 0:
                text = get_text(lang, "no_messages", email=email_data['address'])
                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}")],
                    [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")]
                ]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                text = get_text(lang, "messages_list", count=len(messages), email=email_data['address'])
                await query.edit_message_text(text, reply_markup=get_messages_keyboard(messages, email_index, lang))
        return

    if data.startswith("msg_"):
        parts = data.split("_")
        email_index = int(parts[1])
        msg_index = int(parts[2])

        emails = get_user_emails(user_id)
        if email_index < len(emails):
            email_data = emails[email_index]
            messages = check_inbox(email_data['token'])
            if messages and msg_index < len(messages):
                msg = messages[msg_index]
                msg_id = msg['id']
                full_msg = get_message_content(msg_id, email_data['token'])
                if not full_msg:
                    text = get_text(lang, "error_load_message")
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]))
                    return

                sender = full_msg.get('from', {}).get('address', 'Unknown')
                subject = full_msg.get('subject', 'No Subject')
                date = full_msg.get('createdAt', 'Unknown')
                content = full_msg.get('text', full_msg.get('intro', 'No content'))

                otp = extract_otp(content)
                max_len = 3500
                truncated = content[:max_len] + ("\n\n... (الرسالة طويلة جداً)" if lang == "ar" else "\n\n... (message too long)") if len(content) > max_len else content

                if otp:
                    text = get_text(lang, "otp_found", otp=otp)
                    text += "\n\n" + get_text(lang, "message_detail", sender=sender, subject=subject, date=date, content=truncated)
                else:
                    text = get_text(lang, "message_detail", sender=sender, subject=subject, date=date, content=truncated)

                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]), parse_mode='HTML')
        return

    if data.startswith("view_email_"):
        email_index = int(data.split("_")[2])
        emails = get_user_emails(user_id)
        if email_index < len(emails):
            email_data = emails[email_index]
            text = f"📧 <code>{email_data['address']}</code>\n🔑 <code>TempMail123</code>"
            keyboard = [
                [InlineKeyboardButton(get_text(lang, "btn_inbox"), callback_data=f"inbox_{email_index}")],
                [InlineKeyboardButton(get_text(lang, "btn_delete"), callback_data=f"confirm_delete_{email_index}")],
                [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="my_emails")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return

    if data.startswith("confirm_delete_") and data != "confirm_delete_all":
        email_index = int(data.split("_")[2])
        emails = get_user_emails(user_id)
        if email_index < len(emails):
            email_data = emails[email_index]
            text = get_text(lang, "confirm_delete", email=email_data['address'])
            keyboard = [[
                InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data=f"delete_{email_index}"),
                InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="my_emails")
            ]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("delete_") and not data.startswith("delete_all"):
        email_index = int(data.split("_")[1])
        emails = get_user_emails(user_id)
        if email_index < len(emails):
            email_data = emails[email_index]
            remove_user_email(user_id, email_data['address'])
            text = get_text(lang, "email_deleted", email=email_data['address'])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        return

    if data == "confirm_delete_all":
        emails = get_user_emails(user_id)
        if not emails:
            text = get_text(lang, "no_emails")
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        else:
            text = get_text(lang, "confirm_delete_all", count=len(emails))
            keyboard = [[
                InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data="delete_all"),
                InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="back_to_menu")
            ]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "delete_all":
        emails = get_user_emails(user_id)
        count = len(emails)
        user_id_str = str(user_id)
        user_database[user_id_str]['emails'] = []
        save_single_user(user_id_str, user_database[user_id_str])
        text = get_text(lang, "all_emails_deleted", count=count)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        return

    if data == "stats":
        if not is_admin(user_id):
            return
        emails = get_user_emails(user_id)
        total_messages = 0
        for email_data in emails:
            msgs = check_inbox(email_data['token'])
            if msgs is not None:
                total_messages += len(msgs)
        text = get_text(lang, "stats", total_users=len(user_database), user_emails=len(emails), total_messages=total_messages)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]))
        return

# ============= معالج الرسائل النصية =============

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_offline_message

    user_id = update.effective_user.id
    lang = get_user_language(user_id) or "ar"

    # enforce (صارم)
    if not await enforce_access_or_reply(update, context, lang):
        return

    # توجيه رسائل المستخدم للأدمن (كما هو)
    if forwarding_enabled and user_id != ADMIN_ID:
        try:
            user = update.effective_user
            user_name = user.first_name or ""
            if user.last_name:
                user_name += f" {user.last_name}"
            username = f"@{user.username}" if user.username else "لا يوجد"

            forward_text = f"📨 <b>رسالة جديدة من مستخدم:</b>\n\n"
            forward_text += f"👤 الاسم: {user_name}\n"
            forward_text += f"🆔 المعرف: {username}\n"
            forward_text += f"🔢 ID: <code>{user_id}</code>\n"
            forward_text += f"━━━━━━━━━━━━━━━\n"
            forward_text += f"💬 الرسالة:\n{update.message.text}"

            await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode='HTML')
        except Exception as e:
            print(f"❌ فشل توجيه الرسالة للأدمن: {e}")

    waiting_for = context.user_data.get('waiting_for')
    if not waiting_for:
        return

    # ===== admin flows =====
    if waiting_for == 'welcome_message' and is_admin(user_id):
        txt = (update.message.text or "").strip()
        if txt.lower() in ["حذف", "delete", "remove", "del"]:
            set_setting(WELCOME_KEY, "")
            msg = "✅ تم حذف رسالة الترحيب." if lang == "ar" else "✅ Welcome message removed."
        else:
            set_setting(WELCOME_KEY, txt)
            msg = "✅ تم حفظ رسالة الترحيب." if lang == "ar" else "✅ Welcome message saved."
        context.user_data['waiting_for'] = None
        await update.message.reply_text(msg, reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    if waiting_for == 'ban_user' and is_admin(user_id):
        raw = (update.message.text or "").strip()
        context.user_data['waiting_for'] = None

        # parse: "<id/@user> [reason...]"
        parts = raw.split(maxsplit=1)
        target = parts[0]
        reason = parts[1] if len(parts) > 1 else ""

        found = find_user_by_username_or_id(target, user_database)
        if not found:
            await update.message.reply_text("❌ لم يتم العثور على المستخدم" if lang == "ar" else "❌ User not found",
                                            reply_markup=get_admin_panel_keyboard(lang, user_id))
            return

        tid = int(found['telegram_id'])
        ok = ban_user(tid, user_id, reason)
        if ok:
            await update.message.reply_text(f"✅ تم حظر المستخدم: <code>{tid}</code>" if lang == "ar" else f"✅ User banned: <code>{tid}</code>",
                                            parse_mode='HTML',
                                            reply_markup=get_admin_panel_keyboard(lang, user_id))
        else:
            await update.message.reply_text("❌ فشل الحظر" if lang == "ar" else "❌ Ban failed",
                                            reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    if waiting_for == 'unban_user' and is_admin(user_id):
        raw = (update.message.text or "").strip()
        context.user_data['waiting_for'] = None

        found = find_user_by_username_or_id(raw, user_database)
        if not found:
            await update.message.reply_text("❌ لم يتم العثور على المستخدم" if lang == "ar" else "❌ User not found",
                                            reply_markup=get_admin_panel_keyboard(lang, user_id))
            return

        tid = int(found['telegram_id'])
        ok = unban_user(tid)
        if ok:
            await update.message.reply_text(f"✅ تم فك الحظر عن: <code>{tid}</code>" if lang == "ar" else f"✅ User unbanned: <code>{tid}</code>",
                                            parse_mode='HTML',
                                            reply_markup=get_admin_panel_keyboard(lang, user_id))
        else:
            await update.message.reply_text("❌ هذا المستخدم غير محظور أو فشل الفك" if lang == "ar" else "❌ Not banned or failed",
                                            reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    # تعيين قناة
    if waiting_for == 'channel_username' and is_admin(user_id):
        channel_username = update.message.text.strip().replace('@', '')
        context.user_data['waiting_for'] = None
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            ok = set_channel(channel_username, chat.id, chat.title)
            if ok:
                msg = f"✅ تم تعيين القناة: @{channel_username}\n🆔 <code>{chat.id}</code>\n📢 {chat.title}"
            else:
                msg = "❌ فشل تعيين القناة"
        except Exception as e:
            msg = f"❌ خطأ: {str(e)[:200]}"
        await update.message.reply_text(msg, parse_mode='HTML', reply_markup=get_channel_management_keyboard(lang))
        return

    # تعيين رسالة الاشتراك
    if waiting_for == 'channel_message' and is_admin(user_id):
        msg_text = update.message.text
        channel_username = context.user_data.get('channel_username')
        context.user_data['waiting_for'] = None
        context.user_data['channel_username'] = None
        ok = False
        if channel_username:
            ok = set_channel_message(channel_username, msg_text)
        await update.message.reply_text("✅ تم حفظ رسالة الاشتراك" if ok else "❌ فشل حفظ الرسالة",
                                        reply_markup=get_channel_management_keyboard(lang))
        return

# ============= معالج الأخطاء =============

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    print(f"⚠️ حدث خطأ: {context.error}")

    msg = str(context.error)
    ignorable = ["Query is too old", "query id is invalid", "Message is not modified"]
    if any(x in msg for x in ignorable):
        return

    print("❌ خطأ غير متوقع:")
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

# ============= تشغيل البوت =============

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ Error: Please set TELEGRAM_BOT_TOKEN in environment variables")
        return

    application = Application.builder().token(token).build()

    from telegram.ext import MessageHandler, filters
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    application.add_error_handler(error_handler)

    print("🤖 Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
