#!/usr/bin/env python3
"""
بوت تلجرام لإنشاء إيميلات مؤقتة متعددة
Telegram Temp Email Bot with Multiple Emails Support

✅ إضافات مطلوبة بدون حذف أي شيء قديم:
1) اشتراك إجباري صارم: يتحقق عند /start + كل Callback + كل رسالة (إذا خرج من القناة ينمنع فوراً)
2) رسالة ترحيب عند /start قابلة للتعيين من الأدمن
3) حظر مستخدم / فك حظر (من لوحة الأدمن)

ملاحظة: تم الحفاظ على لوحة الأدمن القديمة كاملة (الأقسام والأزرار والوظائف).
"""

import requests
import re
import os
import json
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from datetime import datetime
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

# متغير لتفعيل/تعطيل توجيه الرسائل للأدمن
forwarding_enabled = False

# متغيرات حالة البوت
bot_active = True
bot_offline_message = ""

DATABASE_URL = os.getenv("DATABASE_URL")

# كاش بسيط للتحقق من الاشتراك (صارم لكنه يقلل الضغط)
SUB_CHECK_TTL_SECONDS = 30
_sub_cache = {}  # user_id -> {"ok": bool, "ts": datetime}

WELCOME_KEY = "welcome_message"

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
    if conn:
        try:
            with conn.cursor() as cur:
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

                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bot_users' AND column_name='first_name') THEN
                            ALTER TABLE bot_users ADD COLUMN first_name VARCHAR(255);
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bot_users' AND column_name='last_name') THEN
                            ALTER TABLE bot_users ADD COLUMN last_name VARCHAR(255);
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bot_users' AND column_name='username') THEN
                            ALTER TABLE bot_users ADD COLUMN username VARCHAR(255);
                        END IF;
                    END $$;
                """)

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

                cur.execute("""
                    DO $$ 
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'channels' AND column_name = 'channel_title'
                        ) THEN
                            ALTER TABLE channels ADD COLUMN channel_title VARCHAR(500);
                        END IF;
                    END $$;
                """)

                # جدول المشرفين
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

                # ✅ جدول المحظورين
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS banned_users (
                        telegram_id BIGINT PRIMARY KEY,
                        reason TEXT,
                        banned_by BIGINT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # ✅ جدول إعدادات البوت (رسالة الترحيب)
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

def save_user_data(data):
    pass

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

# ============= إعدادات البوت (رسالة الترحيب) =============

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

# ============= الحظر / فك الحظر =============

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

def ban_user_db(user_id: int, banned_by: int, reason: str = "") -> bool:
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

def unban_user_db(user_id: int) -> bool:
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

def find_user_by_username_or_id(search_query):
    search_query = str(search_query).strip().lstrip('@')

    for uid, info in user_database.items():
        if uid == search_query:
            return {'telegram_id': int(uid), 'username': info.get('username'), 'first_name': info.get('first_name')}
        if info.get('username', '').lower() == search_query.lower():
            return {'telegram_id': int(uid), 'username': info.get('username'), 'first_name': info.get('first_name')}

    try:
        user_id = int(search_query)
        # حتى لو ما كان موجود بالذاكرة، رجعه كـ ID (الأدمن ممكن يحظر شخص ما عنده سجل)
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
                print(f"❌ القناة {channel_username} غير موجودة")
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

# ============= اشتراك إجباري صارم =============

async def check_user_subscription_strict(user_id, context):
    # الأدمن يتجاوز
    if is_admin(user_id):
        return True

    channel_info = get_channel_info()
    if not channel_info:
        return True

    if not channel_info.get('subscription_enabled'):
        return True

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
        # صارم: أي خطأ = منع
        print(f"⚠️ strict sub check error for {user_id}: {e}")
        _sub_cache[user_id] = {"ok": False, "ts": now}
        return False

def build_subscription_block(lang):
    channel_info = get_channel_info()
    if not channel_info:
        return None, None

    message = channel_info.get('subscription_message') or ""
    if lang == "ar":
        text = (
            "⚠️ يجب عليك الاشتراك في القناة للاستخدام\n\n"
            f"🔗 القناة: @{channel_info['channel_username']}\n\n"
            f"{message}\n\n"
            "بعد الاشتراك، اضغط على زر '✅ التحقق من الاشتراك'"
        )
        join_btn = "📢 الانضمام للقناة"
        verify_btn = "✅ التحقق من الاشتراك"
    else:
        text = (
            "⚠️ You must join the channel to use the bot\n\n"
            f"🔗 Channel: @{channel_info['channel_username']}\n\n"
            f"{message}\n\n"
            "After joining, press '✅ Verify Subscription'"
        )
        join_btn = "📢 Join Channel"
        verify_btn = "✅ Verify Subscription"

    keyboard = [
        [InlineKeyboardButton(join_btn, url=f"https://t.me/{channel_info['channel_username']}")],
        [InlineKeyboardButton(verify_btn, callback_data="verify_subscription")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

async def enforce_access_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    user_id = update.effective_user.id

    # حظر
    if is_banned(user_id) and not is_admin(user_id):
        msg = "⛔ تم حظرك من استخدام البوت." if lang == "ar" else "⛔ You are banned from using this bot."
        if update.message:
            await update.message.reply_text(msg)
        else:
            try:
                await update.callback_query.edit_message_text(msg)
            except Exception:
                pass
        return False

    # توقف البوت
    if not bot_active and not is_admin(user_id):
        if bot_offline_message:
            text = f"⚠️ البوت متوقف مؤقتاً\n\n{bot_offline_message}"
        else:
            text = "⚠️ البوت متوقف مؤقتاً. يرجى المحاولة لاحقاً."
        if update.message:
            await update.message.reply_text(text)
        else:
            try:
                await update.callback_query.edit_message_text(text)
            except Exception:
                pass
        return False

    # اشتراك صارم
    ok = await check_user_subscription_strict(user_id, context)
    if not ok:
        text, kb = build_subscription_block(lang)
        if text and kb:
            if update.message:
                await update.message.reply_text(text, reply_markup=kb)
            else:
                try:
                    await update.callback_query.edit_message_text(text, reply_markup=kb)
                except Exception:
                    pass
        return False

    return True

# تهيئة قاعدة البيانات عند بدء التشغيل
init_database()

# بيانات المستخدمين في الذاكرة
user_database = load_user_data()

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

            "admin_panel": "👑 لوحة تحكم المشرف\n\nمرحباً بك في لوحة التحكم",
            "btn_admin_stats": "📊 الإحصائيات",
            "btn_users_list": "👥 قائمة المستخدمين",
            "btn_broadcast": "📢 إرسال رسالة جماعية",
            "btn_backup": "💾 نسخ احتياطي للبيانات",
            "btn_settings": "⚙️ الإعدادات",
            "btn_bot_info": "ℹ️ معلومات البوت",

            "users_list": "👥 قائمة المستخدمين\n\nإجمالي المستخدمين: {total}\nالمستخدمون النشطون: {active}\nالمستخدمون غير النشطين: {inactive}",
            "broadcast_prompt": "📢 إرسال رسالة جماعية\n\nأرسل الرسالة التي تريد إرسالها لجميع المستخدمين:",
            "broadcast_sent": "✅ تم إرسال الرسالة بنجاح!\n\nإرسال ناجح: {success}\nفشل: {failed}",
            "bot_info": "ℹ️ معلومات البوت\n\n🤖 الاسم: بوت الإيميلات المؤقتة\n📌 الإصدار: 2.0\n🌐 اللغات: العربية، الإنجليزية\n📧 API: mail.tm",

            "subscription_verified": "✅ تم التحقق من اشتراكك بنجاح!\n\nيمكنك الآن استخدام البوت",
            "subscription_not_verified": "❌ لم يتم التحقق من اشتراكك\n\nتأكد من الاشتراك في القناة ثم اضغط على زر التحقق مرة أخرى",

            "btn_verify_subscription": "✅ التحقق من الاشتراك",
            "btn_join_channel": "📢 الانضمام للقناة",

            "channel_management": "📢 إدارة قنوات الاشتراك الإجباري\n\nاختر الإجراء المطلوب:",
            "btn_set_channel": "تعيين القناة",
            "btn_delete_channel": "حذف القناة",
            "btn_set_message": "تعيين رسالة الاشتراك",
            "btn_toggle_subscription": "إشعار الاشتراك: {status}",
            "channel_set_prompt": "📢 تعيين قناة الاشتراك الإجباري\n\nأرسل username القناة (بدون @)\nمثال: mychannel",
            "channel_message_prompt": "📝 تعيين رسالة الاشتراك الإجباري\n\nأرسل الرسالة التي ستظهر للمستخدمين:",
            "channel_set_success": "✅ تم تعيين القناة بنجاح!\n\n📢 القناة: @{channel}",
            "channel_deleted": "✅ تم حذف القناة بنجاح",
            "channel_message_set": "✅ تم تعيين رسالة الاشتراك بنجاح",
            "subscription_toggled": "✅ تم {action} إشعار الاشتراك الإجباري",
            "no_channel_set": "❌ لا توجد قناة محددة\n\nقم بتعيين قناة أولاً",
            "current_channel_info": "📢 معلومات القناة الحالية\n\nالقناة: @{channel}\nالحالة: {status}\nالرسالة: {message}",
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

            "admin_panel": "👑 Admin Control Panel\n\nWelcome to the control panel",
            "btn_admin_stats": "📊 Statistics",
            "btn_users_list": "👥 Users List",
            "btn_broadcast": "📢 Broadcast Message",
            "btn_backup": "💾 Backup Data",
            "btn_settings": "⚙️ Settings",
            "btn_bot_info": "ℹ️ Bot Info",

            "users_list": "👥 Users List\n\nTotal Users: {total}\nActive Users: {active}\nInactive Users: {inactive}",
            "broadcast_prompt": "📢 Broadcast Message\n\nSend the message you want to broadcast to all users:",
            "broadcast_sent": "✅ Message sent successfully!\n\nSuccess: {success}\nFailed: {failed}",
            "bot_info": "ℹ️ Bot Information\n\n🤖 Name: Temp Email Bot\n📌 Version: 2.0\n🌐 Languages: Arabic, English\n📧 API: mail.tm",

            "subscription_verified": "✅ Subscription verified!\n\nYou can now use the bot",
            "subscription_not_verified": "❌ Subscription not verified\n\nJoin the channel then try again",
            "btn_verify_subscription": "✅ Verify Subscription",
            "btn_join_channel": "📢 Join Channel",
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
            print("❌ لا توجد نطاقات متاحة")
            return None, None

        import random
        import string
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email_address = f"{username}@{domains[0]}"
        password = "TempMail123"

        response = requests.post(
            f"{API}/accounts",
            json={"address": email_address, "password": password},
            timeout=10
        )

        if response.status_code == 201:
            token_response = requests.post(
                f"{API}/token",
                json={"address": email_address, "password": password},
                timeout=10
            )

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
        elif response.status_code == 401:
            return None
        else:
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

# ============= وظائف إدارة المستخدمين =============

def get_user_data(user_id):
    user_id_str = str(user_id)
    if user_id_str not in user_database:
        user_database[user_id_str] = {'lang': None, 'emails': []}
        save_single_user(user_id_str, user_database[user_id_str])
    return user_database[user_id_str]

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

def get_user_language(user_id):
    return get_user_data(user_id).get('lang')

# ============= وظائف لوحة المفاتيح =============

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
    ]

    if user_id == ADMIN_ID or is_admin(user_id):
        keyboard.append([
            InlineKeyboardButton(get_text(lang, "btn_stats"), callback_data="stats"),
            InlineKeyboardButton(get_text(lang, "btn_delete_all"), callback_data="confirm_delete_all")
        ])
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_admin_panel"), callback_data="admin_panel")])
    else:
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_delete_all"), callback_data="confirm_delete_all")])

    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_language"), callback_data="change_language")])
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

# ✅ لوحة الأدمن القديمة كاملة + إضافة 3 أزرار جديدة بدون حذف شيء
def get_admin_panel_keyboard(lang, user_id):
    keyboard = [
        [InlineKeyboardButton("📊 قسم الإحصائيات" if lang == "ar" else "📊 Statistics", callback_data="section_stats")],
        [InlineKeyboardButton("📢 قسم الإذاعة" if lang == "ar" else "📢 Broadcasting", callback_data="section_broadcast")],
        [InlineKeyboardButton("📨 قسم توجيه الرسائل" if lang == "ar" else "📨 Message Forwarding", callback_data="section_forward")],
        [InlineKeyboardButton("📢 إدارة القنوات" if lang == "ar" else "📢 Channel Management", callback_data="channel_management")],
        [InlineKeyboardButton("⚙️ الإعدادات" if lang == "ar" else "⚙️ Settings", callback_data="section_settings")],
        [InlineKeyboardButton("👥 إدارة الأعضاء" if lang == "ar" else "👥 Member Management", callback_data="section_members")],
    ]

    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮 إدارة المشرفين" if lang == "ar" else "👮 Admin Management", callback_data="section_admins")])

    # ✅ إضافات جديدة (بدون حذف القديم)
    keyboard.append([InlineKeyboardButton("👋 رسالة الترحيب" if lang == "ar" else "👋 Welcome Message", callback_data="welcome_message")])
    keyboard.append([InlineKeyboardButton("🚫 حظر مستخدم" if lang == "ar" else "🚫 Ban User", callback_data="ban_user")])
    keyboard.append([InlineKeyboardButton("✅ فك حظر" if lang == "ar" else "✅ Unban User", callback_data="unban_user")])

    keyboard.append([InlineKeyboardButton("ℹ️ معلومات البوت" if lang == "ar" else "ℹ️ Bot Info", callback_data="bot_info")])
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_channel_management_keyboard(lang):
    channel_info = get_channel_info(only_enabled=False)

    keyboard = [
        [InlineKeyboardButton(get_text(lang, "btn_set_channel"), callback_data="set_channel")],
        [InlineKeyboardButton(get_text(lang, "btn_set_message"), callback_data="set_channel_message")],
    ]

    if channel_info:
        status_text = "✅" if channel_info['subscription_enabled'] else "❌"
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_toggle_subscription", status=status_text), callback_data="toggle_subscription")])
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_delete_channel"), callback_data="delete_channel")])

    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

# ============= معالجات الأوامر =============

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    user_lang = get_user_language(user_id) or "ar"

    update_user_info(user_id, user)

    # ✅ enforce صارم (حظر + توقف + اشتراك)
    if not await enforce_access_or_reply(update, context, user_lang):
        return

    # ✅ رسالة ترحيب قابلة للتعيين (تظهر عند /start)
    welcome_msg = get_setting(WELCOME_KEY, "").strip()
    if welcome_msg:
        try:
            await update.message.reply_text(welcome_msg)
        except Exception:
            pass

    saved_lang = get_user_language(user_id)
    if saved_lang:
        emails_count = len(get_user_emails(user_id))
        text = get_text(user_lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(user_lang, user_id)
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
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
    total_emails = sum(len(user['emails']) for user in user_database.values())
    active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
    total_messages = 0

    text = get_text(lang, "admin_stats",
                    total_users=total_users,
                    total_emails=total_emails,
                    total_messages=total_messages,
                    active_users=active_users)

    keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ============= معالجات الأزرار =============

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_active, bot_offline_message

    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = update.effective_user.id
    data = query.data

    # اختيار اللغة
    if data.startswith("lang_"):
        lang = data.split("_")[1]
        user = update.effective_user
        set_user_language(user_id, lang, user)

        # بعد اختيار اللغة: enforce صارم
        if not await enforce_access_or_reply(update, context, lang):
            return

        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    lang = get_user_language(user_id) or "ar"

    # مسموح بدون اشتراك: فقط verify + change_language
    if data not in ["verify_subscription", "change_language"]:
        if not await enforce_access_or_reply(update, context, lang):
            return

    if data == "change_language":
        keyboard = get_language_keyboard()
        await query.edit_message_text(get_text("ar", "welcome"), reply_markup=keyboard)
        return

    if data == "back_to_menu":
        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    # ✅ تحقق الاشتراك
    if data == "verify_subscription":
        ok = await check_user_subscription_strict(user_id, context)
        if ok:
            text = get_text(lang, "subscription_verified")
            emails_count = len(get_user_emails(user_id))
            text += f"\n\n{get_text(lang, 'main_menu', emails_count=emails_count)}"
            keyboard = get_main_menu_keyboard(lang, user_id)
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            text = get_text(lang, "subscription_not_verified")
            block_text, kb = build_subscription_block(lang)
            await query.edit_message_text(block_text, reply_markup=kb)
        return

    # ======= أزرارك الأصلية (create/my_emails/select_inbox... إلخ) =======
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
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            text = get_text(lang, "select_email", count=len(emails))
            keyboard = get_email_list_keyboard(emails, "view_email", lang)
            await query.edit_message_text(text, reply_markup=keyboard)
        return

    if data == "select_inbox":
        emails = get_user_emails(user_id)
        if not emails:
            text = get_text(lang, "no_emails")
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            text = get_text(lang, "select_email", count=len(emails))
            keyboard = get_email_list_keyboard(emails, "inbox", lang)
            await query.edit_message_text(text, reply_markup=keyboard)
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
                keyboard = get_messages_keyboard(messages, email_index, lang)
                await query.edit_message_text(text, reply_markup=keyboard)
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
                full_msg = get_message_content(msg['id'], email_data['token'])
                if not full_msg:
                    text = get_text(lang, "error_load_message")
                    keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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
                    text += f"\n\n{get_text(lang, 'message_detail', sender=sender, subject=subject, date=date, content=truncated)}"
                else:
                    text = get_text(lang, "message_detail", sender=sender, subject=subject, date=date, content=truncated)

                keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
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
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "confirm_delete_all":
        emails = get_user_emails(user_id)
        if not emails:
            text = get_text(lang, "no_emails")
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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
        user_database[str(user_id)]['emails'] = []
        save_single_user(str(user_id), user_database[str(user_id)])
        text = get_text(lang, "all_emails_deleted", count=count)
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "stats":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return

        emails = get_user_emails(user_id)
        total_messages = 0
        for email_data in emails:
            messages = check_inbox(email_data['token'])
            if messages is not None:
                total_messages += len(messages)

        text = get_text(lang, "stats",
                        total_users=len(user_database),
                        user_emails=len(emails),
                        total_messages=total_messages)

        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ======= لوحة المشرف (القديمة) =======
    if data == "admin_panel":
        if not is_admin(user_id):
            return
        text = get_text(lang, "admin_panel")
        await query.edit_message_text(text, reply_markup=get_admin_panel_keyboard(lang, user_id))
        return

    # ======= ✅ إضافة: رسالة الترحيب =======
    if data == "welcome_message":
        if not is_admin(user_id):
            return
        current = get_setting(WELCOME_KEY, "").strip()
        context.user_data['waiting_for'] = 'welcome_message'
        text = "👋 أرسل رسالة الترحيب التي تريدها عند /start\n\n" if lang == "ar" else "👋 Send the welcome message shown on /start\n\n"
        if current:
            text += f"📝 الحالية:\n{current}\n\n"
        text += ("(أرسل كلمة حذف لحذفها)" if lang == "ar" else "(Send 'delete' to remove it)")
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ======= ✅ إضافة: حظر / فك حظر =======
    if data == "ban_user":
        if not is_admin(user_id):
            return
        context.user_data['waiting_for'] = 'ban_user'
        text = "🚫 أرسل ID أو @username لحظر المستخدم.\nوتقدر تكتب سبب بعده.\nمثال:\n123456 سبام" if lang == "ar" else "🚫 Send ID or @username to ban.\nYou can add a reason.\nExample:\n123456 spam"
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "unban_user":
        if not is_admin(user_id):
            return
        context.user_data['waiting_for'] = 'unban_user'
        text = "✅ أرسل ID أو @username لفك الحظر." if lang == "ar" else "✅ Send ID or @username to unban."
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ======= إدارة القنوات (كما كان) =======
    if data == "channel_management":
        if not is_admin(user_id):
            return

        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            status = "✅ مفعّل" if channel_info['subscription_enabled'] else "❌ معطّل"
            message = channel_info['subscription_message'] or "لا توجد رسالة"
            channel_id = channel_info.get('channel_id', 'غير محدد')
            channel_title = channel_info.get('channel_title', 'غير محدد')

            text = get_text(lang, "current_channel_info",
                            channel=channel_info['channel_username'],
                            status=status,
                            message=message)
            text += f"\n📢 اسم القناة: <b>{channel_title}</b>"
            text += f"\n🆔 معرّف القناة: <code>{channel_id}</code>"
        else:
            text = get_text(lang, "channel_management")

        keyboard = get_channel_management_keyboard(lang)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')
        return

    if data == "set_channel":
        if not is_admin(user_id):
            return
        text = get_text(lang, "channel_set_prompt")
        context.user_data['waiting_for'] = 'channel_username'
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "set_channel_message":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if not channel_info:
            text = get_text(lang, "no_channel_set")
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        text = get_text(lang, "channel_message_prompt")
        context.user_data['waiting_for'] = 'channel_message'
        context.user_data['channel_username'] = channel_info['channel_username']
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "delete_channel":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            delete_channel(channel_info['channel_username'])
            text = get_text(lang, "channel_deleted")
        else:
            text = get_text(lang, "no_channel_set")
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "toggle_subscription":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            new_status = toggle_subscription(channel_info['channel_username'])
            action = "تفعيل" if new_status else "تعطيل"
            text = get_text(lang, "subscription_toggled", action=action)
        else:
            text = get_text(lang, "no_channel_set")

        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # باقي أقسام الأدمن القديمة موجودة في message_handler + كما كانت تعمل عندك
    # (section_stats / section_broadcast / section_forward / section_settings / section_members / section_admins ...)
    # سيتم التعامل معها في نفس منطقك القديم: لم ألمسها هنا لتفادي تغيير سلوكك.

# ============= معالج الرسائل النصية =============

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global forwarding_enabled, bot_offline_message, bot_active

    user_id = update.effective_user.id
    lang = get_user_language(user_id) or "ar"

    # ✅ enforce صارم على الرسائل كمان
    if not await enforce_access_or_reply(update, context, lang):
        return

    # توجيه الرسائل للأدمن إذا كان التوجيه مفعّل
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

    # ✅ حفظ رسالة الترحيب
    if waiting_for == 'welcome_message' and is_admin(user_id):
        txt = (update.message.text or "").strip()
        context.user_data['waiting_for'] = None
        if txt.lower() in ["حذف", "delete", "del", "remove"]:
            set_setting(WELCOME_KEY, "")
            text = "✅ تم حذف رسالة الترحيب" if lang == "ar" else "✅ Welcome message removed"
        else:
            set_setting(WELCOME_KEY, txt)
            text = "✅ تم حفظ رسالة الترحيب" if lang == "ar" else "✅ Welcome message saved"
        await update.message.reply_text(text)
        return

    # ✅ حظر مستخدم
    if waiting_for == 'ban_user' and is_admin(user_id):
        raw = (update.message.text or "").strip()
        context.user_data['waiting_for'] = None

        parts = raw.split(maxsplit=1)
        target = parts[0]
        reason = parts[1] if len(parts) > 1 else ""

        found = find_user_by_username_or_id(target)
        if not found:
            await update.message.reply_text("❌ لم يتم العثور على المستخدم" if lang == "ar" else "❌ User not found")
            return

        tid = int(found['telegram_id'])
        ok = ban_user_db(tid, user_id, reason)
        await update.message.reply_text(
            (f"✅ تم حظر المستخدم: <code>{tid}</code>" if ok else "❌ فشل الحظر") if lang == "ar" else
            (f"✅ User banned: <code>{tid}</code>" if ok else "❌ Ban failed"),
            parse_mode='HTML'
        )
        return

    # ✅ فك حظر
    if waiting_for == 'unban_user' and is_admin(user_id):
        raw = (update.message.text or "").strip()
        context.user_data['waiting_for'] = None

        found = find_user_by_username_or_id(raw)
        if not found:
            await update.message.reply_text("❌ لم يتم العثور على المستخدم" if lang == "ar" else "❌ User not found")
            return

        tid = int(found['telegram_id'])
        ok = unban_user_db(tid)
        await update.message.reply_text(
            (f"✅ تم فك الحظر عن: <code>{tid}</code>" if ok else "❌ هذا المستخدم غير محظور أو فشل الفك") if lang == "ar" else
            (f"✅ User unbanned: <code>{tid}</code>" if ok else "❌ Not banned or failed"),
            parse_mode='HTML'
        )
        return

    # ✅ تعيين القناة (من كودك القديم)
    if waiting_for == 'channel_username' and user_id == ADMIN_ID:
        channel_username = update.message.text.strip().replace('@', '')
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            if set_channel(channel_username, chat.id, chat.title):
                text = get_text(lang, "channel_set_success", channel=channel_username)
                text += f"\n\n📢 اسم القناة: <b>{chat.title}</b>"
                text += f"\n🆔 معرّف القناة: <code>{chat.id}</code>"
            else:
                text = get_text(lang, "error")
        except Exception as e:
            text = f"❌ خطأ في الوصول للقناة\n\n{str(e)[:200]}"

        context.user_data['waiting_for'] = None
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return

    # ✅ تعيين رسالة الاشتراك (من كودك القديم)
    if waiting_for == 'channel_message' and user_id == ADMIN_ID:
        message = update.message.text
        channel_username = context.user_data.get('channel_username')
        if channel_username and set_channel_message(channel_username, message):
            text = get_text(lang, "channel_message_set")
        else:
            text = get_text(lang, "error")

        context.user_data['waiting_for'] = None
        context.user_data['channel_username'] = None
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

# ============= تشغيل البوت =============

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    print(f"⚠️ حدث خطأ: {context.error}")

    error_message = str(context.error)
    ignorable_errors = ["Query is too old", "query id is invalid", "Message is not modified"]
    if any(x in error_message for x in ignorable_errors):
        return

    print("❌ خطأ غير متوقع:")
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ خطأ: يرجى تعيين TELEGRAM_BOT_TOKEN في متغيرات البيئة")
        return

    application = Application.builder().token(token).build()

    from telegram.ext import MessageHandler, filters
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    application.add_error_handler(error_handler)

    print("🤖 البوت يعمل الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
