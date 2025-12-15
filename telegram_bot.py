#!/usr/bin/env python3
"""
بوت تلجرام لإنشاء إيميلات مؤقتة متعددة
Telegram Temp Email Bot with Multiple Emails Support
"""

import requests
import re
import json
import os
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
# ID المشرف - يمكن تعيينه من متغيرات البيئة أو مباشرة هنا
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "6436207302"))

# متغير لتفعيل/تعطيل توجيه الرسائل للأدمن
forwarding_enabled = False

# متغيرات حالة البوت
bot_active = True  # حالة تشغيل البوت
bot_offline_message = ""  # رسالة عند إيقاف البوت

DATABASE_URL = os.getenv("DATABASE_URL")

# ============= إدارة قاعدة البيانات =============

def get_db_connection():
    """الحصول على اتصال بقاعدة البيانات"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ خطأ في الاتصال بقاعدة البيانات: {e}")
        return None

def init_database():
    """تهيئة قاعدة البيانات"""
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
                
                # إضافة الأعمدة الجديدة إذا لم تكن موجودة
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
                
                # إضافة عمود channel_title إذا لم يكن موجوداً (للترقية من نسخة قديمة)
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
                
                conn.commit()
                print("✅ تم تهيئة قاعدة البيانات بنجاح")
        except Exception as e:
            print(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
            conn.rollback()
        finally:
            conn.close()

def load_user_data():
    """تحميل بيانات المستخدمين من قاعدة البيانات"""
    conn = get_db_connection()
    if not conn:
        return {}
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT telegram_id, language, first_name, last_name, username, emails FROM bot_users")
            rows = cur.fetchall()
            
            # تحويل النتائج إلى نفس تنسيق user_database القديم
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
    """حفظ بيانات المستخدمين إلى قاعدة البيانات - غير مستخدم للسرعة"""
    pass

def save_single_user(telegram_id, user_info):
    """حفظ مستخدم واحد فقط - أسرع بكثير"""
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

# ============= وظائف إدارة المشرفين =============

def get_all_admins():
    """الحصول على قائمة المشرفين"""
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
    """التحقق إذا كان المستخدم مشرفاً"""
    # المشرف الرئيسي دائماً
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
    """إضافة مشرف جديد"""
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
    """إزالة مشرف"""
    # لا يمكن إزالة المشرف الرئيسي
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
    """البحث عن مستخدم بالاسم أو ID"""
    search_query = str(search_query).strip().lstrip('@')
    
    for uid, info in user_database.items():
        if uid == search_query:
            return {'telegram_id': int(uid), 'username': info.get('username'), 'first_name': info.get('first_name')}
        if info.get('username', '').lower() == search_query.lower():
            return {'telegram_id': int(uid), 'username': info.get('username'), 'first_name': info.get('first_name')}
    
    # محاولة كـ ID رقمي
    try:
        user_id = int(search_query)
        if str(user_id) in user_database:
            info = user_database[str(user_id)]
            return {'telegram_id': user_id, 'username': info.get('username'), 'first_name': info.get('first_name')}
    except ValueError:
        pass
    
    return None

# ============= وظائف إدارة القنوات =============

def get_channel_info(only_enabled=True):
    """الحصول على معلومات القناة الإجبارية
    
    Args:
        only_enabled: إذا كان True، يعيد فقط القنوات المفعّلة. 
                     إذا كان False، يعيد أي قناة بغض النظر عن حالتها (للمشرفين)
    """
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
                # للمشرفين: جلب أي قناة بغض النظر عن حالتها
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
    """تعيين قناة الاشتراك الإجباري"""
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
    """تعيين رسالة الاشتراك للقناة"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cur:
            # التحقق من وجود القناة أولاً
            cur.execute("SELECT id FROM channels WHERE channel_username = %s", (channel_username,))
            if not cur.fetchone():
                print(f"❌ القناة {channel_username} غير موجودة")
                return False
            
            # تحديث رسالة القناة
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
    """حذف قناة الاشتراك"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM channels WHERE channel_username = %s
            """, (channel_username,))
            conn.commit()
            return True
    except Exception as e:
        print(f"❌ خطأ في حذف القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def toggle_subscription(channel_username):
    """تفعيل/تعطيل الاشتراك الإجباري"""
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

async def check_user_subscription(user_id, context):
    """التحقق من اشتراك المستخدم في القناة"""
    try:
        # الحصول على معلومات القناة
        channel_info = get_channel_info()
        
        # إذا لم تكن هناك قناة مفعّلة، السماح بالمرور
        if not channel_info:
            print(f"✅ لا توجد قناة إجبارية - السماح للمستخدم {user_id}")
            return True
        
        if not channel_info.get('subscription_enabled'):
            print(f"✅ الاشتراك الإجباري معطل - السماح للمستخدم {user_id}")
            return True
        
        # استخدام channel_id إذا كان متوفراً (أكثر موثوقية من username)
        channel_id = channel_info.get('channel_id')
        channel_username = channel_info['channel_username']
        
        # تحديد المعرّف المستخدم للتحقق
        if channel_id:
            chat_identifier = channel_id
            print(f"🔍 التحقق من اشتراك المستخدم {user_id} في القناة {channel_id} (@{channel_username})")
        else:
            chat_identifier = f"@{channel_username}"
            print(f"🔍 التحقق من اشتراك المستخدم {user_id} في القناة @{channel_username}")
        
        # محاولة الحصول على حالة العضوية
        try:
            member = await context.bot.get_chat_member(chat_identifier, user_id)
            is_member = member.status in ['member', 'administrator', 'creator']
            
            if is_member:
                print(f"✅ المستخدم {user_id} مشترك في القناة @{channel_username}")
            else:
                print(f"❌ المستخدم {user_id} غير مشترك (حالة: {member.status})")
            
            return is_member
            
        except Exception as api_error:
            error_message = str(api_error).lower()
            print(f"⚠️ خطأ في التحقق من الاشتراك: {api_error}")
            
            # الأخطاء المؤقتة - السماح بالمرور
            if any(err in error_message for err in ['timeout', 'flood', 'connection', 'unavailable', 'timed out']):
                print(f"⚠️ خطأ مؤقت - السماح للمستخدم {user_id} بالمرور")
                return True
            
            # أخطاء تتعلق بالقناة (قناة خاصة، غير موجودة، إلخ) - منع الوصول
            if any(err in error_message for err in ['not found', 'chat not found', 'invalid', 'forbidden']):
                print(f"❌ مشكلة في القناة - منع المستخدم {user_id}")
                return False
            
            # أخطاء غير معروفة - السماح بالمرور لتجنب منع المستخدمين
            print(f"⚠️ خطأ غير معروف - السماح للمستخدم {user_id} بالمرور")
            return True
            
    except Exception as e:
        print(f"❌ خطأ غير متوقع في check_user_subscription: {e}")
        # في حالة أي خطأ غير متوقع، السماح بالمرور
        return True

# تهيئة قاعدة البيانات عند بدء التشغيل
init_database()

# بيانات المستخدمين في الذاكرة
user_database = load_user_data()

# ============= النصوص متعددة اللغات =============

def get_text(lang, key, **kwargs):
    """الحصول على النص حسب اللغة"""
    texts = {
        "ar": {
            "welcome": "🎉 مرحباً بك في بوت الإيميلات المؤقتة!\n\n"
                      "اختر لغتك المفضلة:",
            "main_menu": "📬 القائمة الرئيسية\n\n"
                        "عدد الإيميلات النشطة: {emails_count}",
            "email_created": "✅ تم إنشاء بريد إلكتروني جديد!\n\n"
                           "📧 الإيميل: <code>{email}</code>\n\n"
                           "اضغط على الإيميل للنسخ",
            "no_emails": "❌ لا توجد إيميلات نشطة\n\n"
                        "قم بإنشاء إيميل جديد أولاً",
            "select_email": "📋 اختر الإيميل لعرض الرسائل:\n\n"
                          "عدد الإيميلات: {count}",
            "no_messages": "📭 لا توجد رسائل في هذا الإيميل\n\n"
                          "📧 {email}",
            "messages_list": "📬 الرسائل الواردة ({count})\n"
                           "📧 الإيميل: {email}\n\n",
            "message_detail": "✉️ تفاصيل الرسالة\n\n"
                            "📧 من: {sender}\n"
                            "📌 الموضوع: {subject}\n"
                            "📅 التاريخ: {date}\n\n"
                            "📝 المحتوى:\n{content}\n",
            "otp_found": "🔢 تم العثور على رمز OTP:\n\n"
                        "الرمز: <code>{otp}</code>\n\n"
                        "اضغط على الرمز للنسخ",
            "email_deleted": "🗑️ تم حذف الإيميل بنجاح\n\n"
                           "📧 {email}",
            "all_emails_deleted": "🗑️ تم حذف جميع الإيميلات ({count})",
            "confirm_delete": "⚠️ هل أنت متأكد من حذف هذا الإيميل؟\n\n"
                            "📧 {email}",
            "confirm_delete_all": "⚠️ هل أنت متأكد من حذف جميع الإيميلات؟\n\n"
                                "العدد: {count}",
            "stats": "📊 الإحصائيات\n\n"
                    "👤 المستخدمين الكليين: {total_users}\n"
                    "📧 إيميلاتك النشطة: {user_emails}\n"
                    "📬 إجمالي الرسائل: {total_messages}\n"
                    "🌐 اللغة: العربية",
            "admin_stats": "👑 إحصائيات المشرف\n\n"
                          "👥 إجمالي المستخدمين: {total_users}\n"
                          "📧 إجمالي الإيميلات: {total_emails}\n"
                          "📬 إجمالي الرسائل: {total_messages}\n"
                          "🔄 المستخدمون النشطون: {active_users}",
            "language_changed": "✅ تم تغيير اللغة إلى العربية",
            "error": "❌ حدث خطأ، حاول مرة أخرى",
            "error_create_email": "❌ فشل إنشاء الإيميل\n\n"
                                 "قد تكون الخدمة مشغولة حالياً.\n"
                                 "الرجاء المحاولة مرة أخرى.",
            "error_load_messages": "❌ فشل تحميل الرسائل\n\n"
                                  "قد يكون الاتصال بالخدمة بطيئاً.\n"
                                  "اضغط 🔄 تحديث للمحاولة مرة أخرى.",
            "error_load_message": "❌ فشل تحميل الرسالة\n\n"
                                "حاول مرة أخرى لاحقاً.",
            "unauthorized": "⛔ عذراً، هذا الأمر متاح للمشرف فقط",
            
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
            
            # لوحة تحكم المشرف
            "admin_panel": "👑 لوحة تحكم المشرف\n\n"
                          "مرحباً بك في لوحة التحكم",
            "btn_admin_stats": "📊 الإحصائيات",
            "btn_users_list": "👥 قائمة المستخدمين",
            "btn_broadcast": "📢 إرسال رسالة جماعية",
            "btn_backup": "💾 نسخ احتياطي للبيانات",
            "btn_settings": "⚙️ الإعدادات",
            "btn_bot_info": "ℹ️ معلومات البوت",
            "users_list": "👥 قائمة المستخدمين\n\n"
                         "إجمالي المستخدمين: {total}\n"
                         "المستخدمون النشطون: {active}\n"
                         "المستخدمون غير النشطين: {inactive}",
            "broadcast_prompt": "📢 إرسال رسالة جماعية\n\n"
                               "أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:",
            "broadcast_sent": "✅ تم إرسال الرسالة بنجاح!\n\n"
                             "إرسال ناجح: {success}\n"
                             "فشل: {failed}",
            "backup_success": "✅ تم إنشاء نسخة احتياطية!\n\n"
                            "📁 الملف: {filename}\n"
                            "📊 حجم البيانات: {size}",
            "bot_info": "ℹ️ معلومات البوت\n\n"
                       "🤖 الاسم: بوت الإيميلات المؤقتة\n"
                       "📌 الإصدار: 2.0\n"
                       "👨‍💻 المطور: Replit Agent\n"
                       "🌐 اللغات: العربية، الإنجليزية\n"
                       "📧 API: mail.tm",
            
            # نصوص الاشتراك الإجباري
            "subscription_required": "⚠️ يجب عليك الاشتراك في القناة للاستخدام\n\n"
                                   "🔗 القناة: {channel}\n\n"
                                   "{message}\n\n"
                                   "بعد الاشتراك، اضغط على زر 'التحقق من الاشتراك'",
            "subscription_verified": "✅ تم التحقق من اشتراكك بنجاح!\n\n"
                                   "يمكنك الآن استخدام البوت",
            "subscription_not_verified": "❌ لم يتم التحقق من اشتراكك\n\n"
                                       "تأكد من الاشتراك في القناة ثم اضغط على زر التحقق مرة أخرى",
            "btn_verify_subscription": "✅ التحقق من الاشتراك",
            "btn_join_channel": "📢 الانضمام للقناة",
            
            # نصوص إدارة القنوات
            "channel_management": "📢 إدارة قنوات الاشتراك الإجباري\n\n"
                                "اختر الإجراء المطلوب:",
            "btn_set_channel": "تعيين القناة",
            "btn_delete_channel": "حذف القناة",
            "btn_set_message": "تعيين رسالة الاشتراك",
            "btn_toggle_subscription": "إشعار الاشتراك: {status}",
            "channel_set_prompt": "📢 تعيين قناة الاشتراك الإجباري\n\n"
                                "أرسل username القناة (بدون @)\n"
                                "مثال: mychannel",
            "channel_message_prompt": "📝 تعيين رسالة الاشتراك الإجباري\n\n"
                                    "أرسل الرسالة التي ستظهر للمستخدمين:",
            "channel_set_success": "✅ تم تعيين القناة بنجاح!\n\n"
                                 "📢 القناة: @{channel}",
            "channel_deleted": "✅ تم حذف القناة بنجاح",
            "channel_message_set": "✅ تم تعيين رسالة الاشتراك بنجاح",
            "subscription_toggled": "✅ تم {action} إشعار الاشتراك الإجباري",
            "no_channel_set": "❌ لا توجد قناة محددة\n\n"
                            "قم بتعيين قناة أولاً",
            "current_channel_info": "📢 معلومات القناة الحالية\n\n"
                                  "القناة: @{channel}\n"
                                  "الحالة: {status}\n"
                                  "الرسالة: {message}",
        },
        "en": {
            "welcome": "🎉 Welcome to Temp Email Bot!\n\n"
                      "Choose your preferred language:",
            "main_menu": "📬 Main Menu\n\n"
                        "Active emails: {emails_count}",
            "email_created": "✅ New email created successfully!\n\n"
                           "📧 Email: <code>{email}</code>\n\n"
                           "Tap to copy",
            "no_emails": "❌ No active emails\n\n"
                        "Create a new email first",
            "select_email": "📋 Select email to view messages:\n\n"
                          "Total emails: {count}",
            "no_messages": "📭 No messages in this email\n\n"
                          "📧 {email}",
            "messages_list": "📬 Inbox ({count})\n"
                           "📧 Email: {email}\n\n",
            "message_detail": "✉️ Message Details\n\n"
                            "📧 From: {sender}\n"
                            "📌 Subject: {subject}\n"
                            "📅 Date: {date}\n\n"
                            "📝 Content:\n{content}\n",
            "otp_found": "🔢 OTP Code Found:\n\n"
                        "Code: <code>{otp}</code>\n\n"
                        "Tap to copy",
            "email_deleted": "🗑️ Email deleted successfully\n\n"
                           "📧 {email}",
            "all_emails_deleted": "🗑️ All emails deleted ({count})",
            "confirm_delete": "⚠️ Are you sure you want to delete this email?\n\n"
                            "📧 {email}",
            "confirm_delete_all": "⚠️ Are you sure you want to delete all emails?\n\n"
                                "Count: {count}",
            "stats": "📊 Statistics\n\n"
                    "👤 Total Users: {total_users}\n"
                    "📧 Your Active Emails: {user_emails}\n"
                    "📬 Total Messages: {total_messages}\n"
                    "🌐 Language: English",
            "admin_stats": "👑 Admin Statistics\n\n"
                          "👥 Total Users: {total_users}\n"
                          "📧 Total Emails: {total_emails}\n"
                          "📬 Total Messages: {total_messages}\n"
                          "🔄 Active Users: {active_users}",
            "language_changed": "✅ Language changed to English",
            "error": "❌ An error occurred, please try again",
            "error_create_email": "❌ Failed to create email\n\n"
                                 "The service may be busy.\n"
                                 "Please try again.",
            "error_load_messages": "❌ Failed to load messages\n\n"
                                  "Connection may be slow.\n"
                                  "Press 🔄 Refresh to try again.",
            "error_load_message": "❌ Failed to load message\n\n"
                                "Please try again later.",
            "unauthorized": "⛔ Sorry, this command is for admin only",
            
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
            "admin_panel": "👑 Admin Control Panel\n\n"
                          "Welcome to the control panel",
            "btn_admin_stats": "📊 Statistics",
            "btn_users_list": "👥 Users List",
            "btn_broadcast": "📢 Broadcast Message",
            "btn_backup": "💾 Backup Data",
            "btn_settings": "⚙️ Settings",
            "btn_bot_info": "ℹ️ Bot Info",
            "users_list": "👥 Users List\n\n"
                         "Total Users: {total}\n"
                         "Active Users: {active}\n"
                         "Inactive Users: {inactive}",
            "broadcast_prompt": "📢 Broadcast Message\n\n"
                               "Send the message you want to broadcast to all users:",
            "broadcast_sent": "✅ Message sent successfully!\n\n"
                             "Success: {success}\n"
                             "Failed: {failed}",
            "backup_success": "✅ Backup created!\n\n"
                            "📁 File: {filename}\n"
                            "📊 Data size: {size}",
            "bot_info": "ℹ️ Bot Information\n\n"
                       "🤖 Name: Temp Email Bot\n"
                       "📌 Version: 2.0\n"
                       "👨‍💻 Developer: Replit Agent\n"
                       "🌐 Languages: Arabic, English\n"
                       "📧 API: mail.tm",
        }
    }
    
    text = texts.get(lang, texts["ar"]).get(key, "")
    return text.format(**kwargs) if kwargs else text

# ============= وظائف API =============

def get_available_domains():
    """الحصول على النطاقات المتاحة"""
    try:
        response = requests.get(f"{API}/domains", timeout=10)
        if response.status_code == 200:
            try:
                data = response.json()
                domains = data.get('hydra:member', [])
                return [d['domain'] for d in domains] if domains else []
            except (ValueError, KeyError) as e:
                print(f"⚠️ خطأ في تحليل JSON للنطاقات: {e}")
                return []
    except requests.exceptions.Timeout:
        print("⚠️ انتهت مهلة الحصول على النطاقات")
    except Exception as e:
        print(f"⚠️ خطأ في الحصول على النطاقات: {e}")
    return []

def create_email():
    """إنشاء حساب بريد إلكتروني جديد"""
    try:
        domains = get_available_domains()
        if not domains:
            print("❌ لا توجد نطاقات متاحة")
            return None, None
        
        # إنشاء اسم مستخدم عشوائي
        import random
        import string
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email_address = f"{username}@{domains[0]}"
        password = "TempMail123"
        
        # إنشاء الحساب
        response = requests.post(
            f"{API}/accounts",
            json={"address": email_address, "password": password},
            timeout=10
        )
        
        if response.status_code == 201:
            # الحصول على التوكن
            token_response = requests.post(
                f"{API}/token",
                json={"address": email_address, "password": password},
                timeout=10
            )
            
            if token_response.status_code == 200:
                try:
                    token_data = token_response.json()
                    token = token_data.get('token')
                    if token:
                        print(f"✅ تم إنشاء إيميل: {email_address}")
                        return email_address, token
                    else:
                        print("❌ التوكن غير موجود في الرد")
                except (ValueError, KeyError) as e:
                    print(f"❌ خطأ في تحليل JSON للتوكن: {e}")
            else:
                print(f"❌ فشل الحصول على التوكن: {token_response.status_code}")
        else:
            print(f"❌ فشل إنشاء الإيميل: {response.status_code}")
    except requests.exceptions.Timeout:
        print("❌ انتهت مهلة الاتصال بالخدمة")
    except Exception as e:
        print(f"❌ خطأ في إنشاء الإيميل: {e}")
    
    return None, None

def check_inbox(token):
    """فحص صندوق الوارد
    
    Returns:
        list: قائمة بالرسائل إذا نجح
        None: إذا فشل الطلب (للتمييز عن "لا توجد رسائل")
    """
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{API}/messages", headers=headers, timeout=10)
        
        if response.status_code == 200:
            try:
                data = response.json()
                return data.get('hydra:member', [])
            except (ValueError, KeyError) as e:
                print(f"⚠️ خطأ في تحليل JSON للرسائل: {e}")
                return None  # فشل
        elif response.status_code == 401:
            print("⚠️ التوكن غير صالح أو منتهي")
            return None  # فشل
        else:
            print(f"⚠️ خطأ في فحص الصندوق: {response.status_code}")
            return None  # فشل
    except requests.exceptions.Timeout:
        print("⚠️ انتهت مهلة الاتصال")
        return None  # فشل
    except Exception as e:
        print(f"⚠️ خطأ في فحص الصندوق: {e}")
        return None  # فشل

def get_message_content(message_id, token):
    """الحصول على محتوى الرسالة"""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{API}/messages/{message_id}", headers=headers, timeout=10)
        
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as e:
                print(f"⚠️ خطأ في تحليل JSON للرسالة: {e}")
                return None
        else:
            print(f"⚠️ خطأ في الحصول على الرسالة: {response.status_code}")
    except requests.exceptions.Timeout:
        print("⚠️ انتهت مهلة الاتصال")
    except Exception as e:
        print(f"⚠️ خطأ في الحصول على الرسالة: {e}")
    
    return None

def extract_otp(text):
    """استخراج رموز OTP من النص"""
    if not text:
        return None
    
    # البحث عن أرقام من 4-8 خانات
    match = re.search(r'\b(\d{4,8})\b', text)
    return match.group(1) if match else None

# ============= وظائف إدارة المستخدمين =============

def get_user_data(user_id):
    """الحصول على بيانات المستخدم"""
    user_id_str = str(user_id)
    if user_id_str not in user_database:
        user_database[user_id_str] = {
            'lang': None,  # لم يتم تحديد اللغة بعد
            'emails': []
        }
        save_single_user(user_id_str, user_database[user_id_str])
    return user_database[user_id_str]

def set_user_language(user_id, lang, user_info=None):
    """تعيين لغة المستخدم مع معلوماته"""
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['lang'] = lang
    
    # حفظ معلومات المستخدم إذا توفرت
    if user_info:
        user_data['first_name'] = user_info.first_name or ''
        user_data['last_name'] = user_info.last_name or ''
        user_data['username'] = user_info.username or ''
    
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def update_user_info(user_id, user_info):
    """تحديث معلومات المستخدم"""
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['first_name'] = user_info.first_name or ''
    user_data['last_name'] = user_info.last_name or ''
    user_data['username'] = user_info.username or ''
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def add_user_email(user_id, email, token):
    """إضافة إيميل للمستخدم"""
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['emails'].append({
        'address': email,
        'token': token
    })
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def remove_user_email(user_id, email):
    """حذف إيميل من قائمة المستخدم"""
    user_id_str = str(user_id)
    user_data = get_user_data(user_id)
    user_data['emails'] = [e for e in user_data['emails'] if e['address'] != email]
    user_database[user_id_str] = user_data
    save_single_user(user_id_str, user_data)

def get_user_emails(user_id):
    """الحصول على إيميلات المستخدم"""
    return get_user_data(user_id).get('emails', [])

def get_user_language(user_id):
    """الحصول على لغة المستخدم"""
    return get_user_data(user_id).get('lang')

# ============= وظائف لوحة المفاتيح =============

def get_language_keyboard():
    """لوحة مفاتيح اختيار اللغة"""
    keyboard = [
        [
            InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu_keyboard(lang, user_id):
    """لوحة مفاتيح القائمة الرئيسية"""
    keyboard = [
        [InlineKeyboardButton(get_text(lang, "btn_create"), callback_data="create_email")],
        [
            InlineKeyboardButton(get_text(lang, "btn_my_emails"), callback_data="my_emails"),
            InlineKeyboardButton(get_text(lang, "btn_inbox"), callback_data="select_inbox")
        ],
    ]
    
    # إضافة زر الإحصائيات للمشرف فقط
    if user_id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton(get_text(lang, "btn_stats"), callback_data="stats"),
            InlineKeyboardButton(get_text(lang, "btn_delete_all"), callback_data="confirm_delete_all")
        ])
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_admin_panel"), callback_data="admin_panel")])
    else:
        # للمستخدمين العاديين: فقط زر حذف الكل
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_delete_all"), callback_data="confirm_delete_all")])
    
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_language"), callback_data="change_language")])
    
    return InlineKeyboardMarkup(keyboard)

def get_email_list_keyboard(emails, action_prefix, lang):
    """لوحة مفاتيح قائمة الإيميلات"""
    keyboard = []
    for i, email_data in enumerate(emails):
        email = email_data['address']
        # عرض أول 30 حرف من الإيميل
        display_email = email if len(email) <= 30 else email[:27] + "..."
        keyboard.append([InlineKeyboardButton(
            f"📧 {display_email}",
            callback_data=f"{action_prefix}_{i}"
        )])
    
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_messages_keyboard(messages, email_index, lang):
    """لوحة مفاتيح قائمة الرسائل"""
    keyboard = []
    for i, msg in enumerate(messages[:10]):  # عرض أول 10 رسائل
        subject = msg.get('subject', 'No Subject')
        display_subject = subject if len(subject) <= 30 else subject[:27] + "..."
        keyboard.append([InlineKeyboardButton(
            f"✉️ {display_subject}",
            callback_data=f"msg_{email_index}_{i}"
        )])
    
    keyboard.append([
        InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}"),
        InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel_keyboard(lang, user_id):
    """لوحة مفاتيح لوحة تحكم المشرف"""
    keyboard = [
        [InlineKeyboardButton("📊 قسم الإحصائيات" if lang == "ar" else "📊 Statistics", callback_data="section_stats")],
        [InlineKeyboardButton("📢 قسم الإذاعة" if lang == "ar" else "📢 Broadcasting", callback_data="section_broadcast")],
        [InlineKeyboardButton("📨 قسم توجيه الرسائل" if lang == "ar" else "📨 Message Forwarding", callback_data="section_forward")],
        [InlineKeyboardButton("📢 إدارة القنوات" if lang == "ar" else "📢 Channel Management", callback_data="channel_management")],
        [InlineKeyboardButton("⚙️ الإعدادات" if lang == "ar" else "⚙️ Settings", callback_data="section_settings")],
        [InlineKeyboardButton("👥 إدارة الأعضاء" if lang == "ar" else "👥 Member Management", callback_data="section_members")],
    ]
    
    # قسم إدارة المشرفين (للمشرف الرئيسي فقط)
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮 إدارة المشرفين" if lang == "ar" else "👮 Admin Management", callback_data="section_admins")])
    
    keyboard.append([InlineKeyboardButton("ℹ️ معلومات البوت" if lang == "ar" else "ℹ️ Bot Info", callback_data="bot_info")])
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")])
    
    return InlineKeyboardMarkup(keyboard)

def get_channel_management_keyboard(lang):
    """لوحة مفاتيح إدارة القنوات"""
    channel_info = get_channel_info()
    
    keyboard = [
        [InlineKeyboardButton(get_text(lang, "btn_set_channel"), callback_data="set_channel")],
        [InlineKeyboardButton(get_text(lang, "btn_set_message"), callback_data="set_channel_message")],
    ]
    
    if channel_info:
        status_text = "✅" if channel_info['subscription_enabled'] else "❌"
        keyboard.append([InlineKeyboardButton(
            get_text(lang, "btn_toggle_subscription", status=status_text),
            callback_data="toggle_subscription"
        )])
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_delete_channel"), callback_data="delete_channel")])
    
    keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

# ============= معالجات الأوامر =============

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start"""
    user_id = update.effective_user.id
    user = update.effective_user
    user_lang = get_user_language(user_id) or "ar"
    
    # تحديث معلومات المستخدم دائماً
    update_user_info(user_id, user)
    
    # التحقق من حالة البوت (المشرف دائماً يمكنه الوصول)
    if not bot_active and user_id != ADMIN_ID:
        if bot_offline_message:
            text = f"⚠️ البوت متوقف مؤقتاً\n\n{bot_offline_message}"
        else:
            text = "⚠️ البوت متوقف مؤقتاً. يرجى المحاولة لاحقاً."
        await update.message.reply_text(text)
        return
    
    # معلومات المستخدم للإشعار
    user_name = user.first_name or "غير معروف"
    username = f"@{user.username}" if user.username else "بدون اسم مستخدم"
    
    # تحقق من وجود لغة محفوظة
    saved_lang = get_user_language(user_id)
    
    # إذا كانت اللغة محددة مسبقاً
    if saved_lang:
        # التحقق من الاشتراك الإجباري (إلا للمشرف)
        if not is_admin(user_id):
            is_subscribed = await check_user_subscription(user_id, context)
            if not is_subscribed:
                channel_info = get_channel_info()
                if channel_info:
                    message = channel_info['subscription_message'] or ""
                    text = get_text(user_lang, "subscription_required", 
                                  channel=f"@{channel_info['channel_username']}",
                                  message=message)
                    keyboard = [
                        [InlineKeyboardButton(get_text(user_lang, "btn_join_channel"), 
                                            url=f"https://t.me/{channel_info['channel_username']}")],
                        [InlineKeyboardButton(get_text(user_lang, "btn_verify_subscription"), 
                                            callback_data="verify_subscription")]
                    ]
                    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
                    return
        
        # إذا مشترك أو مشرف، عرض القائمة الرئيسية
        emails_count = len(get_user_emails(user_id))
        text = get_text(user_lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(user_lang, user_id)
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
        # مستخدم جديد - إرسال إشعار للمشرف
        try:
            admin_notification = f"🆕 مستخدم جديد دخل للبوت!\n\n"
            admin_notification += f"👤 الاسم: {user_name}\n"
            admin_notification += f"📱 اسم المستخدم: {username}\n"
            admin_notification += f"🆔 الآيدي: <code>{user_id}</code>\n"
            admin_notification += f"⏰ الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_notification,
                parse_mode='HTML'
            )
        except Exception as e:
            print(f"⚠️ خطأ في إرسال إشعار للمشرف: {e}")
        
        # اختيار اللغة لأول مرة فقط
        text = get_text("ar", "welcome")
        keyboard = get_language_keyboard()
        await update.message.reply_text(text, reply_markup=keyboard)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /admin للمشرف فقط"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(get_text("ar", "unauthorized"))
        return
    
    lang = get_user_language(user_id) or "ar"
    
    # حساب الإحصائيات بسرعة
    total_users = len(user_database)
    total_emails = sum(len(user['emails']) for user in user_database.values())
    active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
    
    # تقدير إجمالي الرسائل (بدون استدعاء API لتسريع الاستجابة)
    # يمكن حساب عدد دقيق لاحقاً إذا لزم الأمر
    total_messages = 0  # سيتم حسابه عند الحاجة
    
    text = get_text(lang, "admin_stats",
                   total_users=total_users,
                   total_emails=total_emails,
                   total_messages=total_messages,
                   active_users=active_users)
    
    keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ============= معالجات الأزرار =============

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج ضغطات الأزرار"""
    global forwarding_enabled, bot_active, bot_offline_message
    
    query = update.callback_query
    
    # الإجابة السريعة على الاستعلام لتجنب timeout
    try:
        await query.answer()
    except Exception:
        pass  # تجاهل أخطاء query.answer القديمة
    
    user_id = update.effective_user.id
    data = query.data
    
    # اختيار اللغة
    if data.startswith("lang_"):
        lang = data.split("_")[1]
        user = update.effective_user
        set_user_language(user_id, lang, user)
        
        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    
    # الحصول على لغة المستخدم
    lang = get_user_language(user_id) or "ar"
    
    # تغيير اللغة
    if data == "change_language":
        keyboard = get_language_keyboard()
        await query.edit_message_text(get_text("ar", "welcome"), reply_markup=keyboard)
        return
    
    # الرجوع للقائمة الرئيسية
    if data == "back_to_menu":
        # التحقق من الاشتراك الإجباري (إلا للمشرف)
        if not is_admin(user_id):
            is_subscribed = await check_user_subscription(user_id, context)
            if not is_subscribed:
                channel_info = get_channel_info()
                if channel_info:
                    message = channel_info['subscription_message'] or ""
                    text = get_text(lang, "subscription_required", 
                                  channel=f"@{channel_info['channel_username']}",
                                  message=message)
                    keyboard = [
                        [InlineKeyboardButton(get_text(lang, "btn_join_channel"), 
                                            url=f"https://t.me/{channel_info['channel_username']}")],
                        [InlineKeyboardButton(get_text(lang, "btn_verify_subscription"), 
                                            callback_data="verify_subscription")]
                    ]
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
                    return
        
        emails_count = len(get_user_emails(user_id))
        text = get_text(lang, "main_menu", emails_count=emails_count)
        keyboard = get_main_menu_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    
    # التحقق من الاشتراك الإجباري لجميع الميزات الرئيسية (إلا للمشرف)
    if data in ["create_email", "my_emails", "select_inbox"] and user_id != ADMIN_ID:
        is_subscribed = await check_user_subscription(user_id, context)
        if not is_subscribed:
            channel_info = get_channel_info()
            if channel_info:
                message = channel_info['subscription_message'] or ""
                text = get_text(lang, "subscription_required", 
                              channel=f"@{channel_info['channel_username']}",
                              message=message)
                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "btn_join_channel"), 
                                        url=f"https://t.me/{channel_info['channel_username']}")],
                    [InlineKeyboardButton(get_text(lang, "btn_verify_subscription"), 
                                        callback_data="verify_subscription")]
                ]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
                return
    
    # إنشاء إيميل جديد
    if data == "create_email":
        email, token = create_email()
        if email and token:
            add_user_email(user_id, email, token)
            text = get_text(lang, "email_created", email=email)
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else:
            # عرض رسالة خطأ واضحة للمستخدم
            text = get_text(lang, "error_create_email")
            keyboard = [
                [InlineKeyboardButton(get_text(lang, "btn_create"), callback_data="create_email")],
                [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # عرض إيميلات المستخدم
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
    
    # اختيار إيميل للصندوق الوارد
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
    
    # عرض صندوق الوارد لإيميل معين
    if data.startswith("inbox_"):
        email_index = int(data.split("_")[1])
        emails = get_user_emails(user_id)
        
        if email_index < len(emails):
            email_data = emails[email_index]
            messages = check_inbox(email_data['token'])
            
            if messages is None:
                # فشل تحميل الرسائل - عرض رسالة خطأ
                text = get_text(lang, "error_load_messages")
                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}")],
                    [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")]
                ]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            elif len(messages) == 0:
                # لا توجد رسائل (ولكن الطلب نجح)
                text = get_text(lang, "no_messages", email=email_data['address'])
                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "btn_refresh"), callback_data=f"inbox_{email_index}")],
                    [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="select_inbox")]
                ]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                # توجد رسائل
                text = get_text(lang, "messages_list", count=len(messages), email=email_data['address'])
                keyboard = get_messages_keyboard(messages, email_index, lang)
                await query.edit_message_text(text, reply_markup=keyboard)
        return
    
    # عرض تفاصيل رسالة
    if data.startswith("msg_"):
        parts = data.split("_")
        email_index = int(parts[1])
        msg_index = int(parts[2])
        
        emails = get_user_emails(user_id)
        if email_index < len(emails):
            email_data = emails[email_index]
            messages = check_inbox(email_data['token'])
            
            if msg_index < len(messages):
                msg = messages[msg_index]
                msg_id = msg['id']
                
                # الحصول على تفاصيل الرسالة
                full_msg = get_message_content(msg_id, email_data['token'])
                
                if not full_msg:
                    # عرض رسالة خطأ واضحة
                    text = get_text(lang, "error_load_message")
                    keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
                    return
                
                if full_msg:
                    sender = full_msg.get('from', {}).get('address', 'Unknown')
                    subject = full_msg.get('subject', 'No Subject')
                    date = full_msg.get('createdAt', 'Unknown')
                    content = full_msg.get('text', full_msg.get('intro', 'No content'))
                    
                    # البحث عن OTP
                    otp = extract_otp(content)
                    
                    # عرض الرسالة الكاملة (حتى 3500 حرف لتجنب حد تيليجرام 4096)
                    max_content_length = 3500
                    if len(content) > max_content_length:
                        truncated_content = content[:max_content_length] + "\n\n... (الرسالة طويلة جداً)" if lang == "ar" else content[:max_content_length] + "\n\n... (message too long)"
                    else:
                        truncated_content = content
                    
                    if otp:
                        text = get_text(lang, "otp_found", otp=otp)
                        text += f"\n\n{get_text(lang, 'message_detail', sender=sender, subject=subject, date=date, content=truncated_content)}"
                    else:
                        text = get_text(lang, "message_detail", 
                                      sender=sender, 
                                      subject=subject, 
                                      date=date, 
                                      content=truncated_content)
                    
                    keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data=f"inbox_{email_index}")]]
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # عرض تفاصيل إيميل
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
    
    # تأكيد حذف إيميل واحد
    if data.startswith("confirm_delete_") and data != "confirm_delete_all":
        email_index = int(data.split("_")[2])
        emails = get_user_emails(user_id)
        
        if email_index < len(emails):
            email_data = emails[email_index]
            text = get_text(lang, "confirm_delete", email=email_data['address'])
            keyboard = [
                [
                    InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data=f"delete_{email_index}"),
                    InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="my_emails")
                ]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # حذف إيميل
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
    
    # تأكيد حذف جميع الإيميلات
    if data == "confirm_delete_all":
        emails = get_user_emails(user_id)
        if not emails:
            text = get_text(lang, "no_emails")
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            text = get_text(lang, "confirm_delete_all", count=len(emails))
            keyboard = [
                [
                    InlineKeyboardButton(get_text(lang, "btn_confirm"), callback_data="delete_all"),
                    InlineKeyboardButton(get_text(lang, "btn_cancel"), callback_data="back_to_menu")
                ]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # حذف جميع الإيميلات
    if data == "delete_all":
        emails = get_user_emails(user_id)
        count = len(emails)
        user_id_str = str(user_id)
        user_database[user_id_str]['emails'] = []
        save_single_user(user_id_str, user_database[user_id_str])
        
        text = get_text(lang, "all_emails_deleted", count=count)
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # الإحصائيات (للمشرف فقط)
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
            # تجاهل الأخطاء في الإحصائيات، فقط احسب ما نجح
            if messages is not None:
                total_messages += len(messages)
        
        text = get_text(lang, "stats",
                       total_users=len(user_database),
                       user_emails=len(emails),
                       total_messages=total_messages)
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="back_to_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # لوحة المشرف
    if data == "admin_panel":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        text = get_text(lang, "admin_panel")
        keyboard = get_admin_panel_keyboard(lang, user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    
    # إدارة القنوات
    if data == "channel_management":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        # جلب معلومات القناة (حتى لو معطّلة)
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
    
    # تعيين قناة جديدة
    if data == "set_channel":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        text = get_text(lang, "channel_set_prompt")
        context.user_data['waiting_for'] = 'channel_username'
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # تعيين رسالة الاشتراك
    if data == "set_channel_message":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
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
    
    # حذف القناة
    if data == "delete_channel":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
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
    
    # تبديل حالة الاشتراك
    if data == "toggle_subscription":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
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
    
    # التحقق من الاشتراك
    if data == "verify_subscription":
        print(f"🔄 المستخدم {user_id} يحاول التحقق من الاشتراك...")
        
        # محاولة التحقق من الاشتراك
        is_subscribed = await check_user_subscription(user_id, context)
        
        if is_subscribed:
            print(f"✅ تم التحقق من اشتراك المستخدم {user_id} بنجاح")
            text = get_text(lang, "subscription_verified")
            emails_count = len(get_user_emails(user_id))
            text += f"\n\n{get_text(lang, 'main_menu', emails_count=emails_count)}"
            keyboard = get_main_menu_keyboard(lang, user_id)
        else:
            print(f"❌ المستخدم {user_id} غير مشترك")
            text = get_text(lang, "subscription_not_verified")
            channel_info = get_channel_info()
            
            if channel_info:
                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "btn_join_channel"), 
                                        url=f"https://t.me/{channel_info['channel_username']}")],
                    [InlineKeyboardButton(get_text(lang, "btn_verify_subscription"), 
                                        callback_data="verify_subscription")]
                ]
            else:
                # في حالة عدم وجود قناة، عرض القائمة الرئيسية
                print(f"⚠️ لا توجد قناة مفعّلة - السماح للمستخدم {user_id}")
                emails_count = len(get_user_emails(user_id))
                text = get_text(lang, "main_menu", emails_count=emails_count)
                keyboard = get_main_menu_keyboard(lang, user_id)
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # إحصائيات المشرف
    if data == "admin_stats":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        # حساب الإحصائيات بسرعة (بدون استدعاء API)
        total_users = len(user_database)
        total_emails = sum(len(user['emails']) for user in user_database.values())
        active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
        total_messages = 0
        
        text = get_text(lang, "admin_stats",
                       total_users=total_users,
                       total_emails=total_emails,
                       total_messages=total_messages,
                       active_users=active_users)
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قائمة المستخدمين
    if data == "users_list":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        total_users = len(user_database)
        active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
        inactive_users = total_users - active_users
        
        text = get_text(lang, "users_list",
                       total=total_users,
                       active=active_users,
                       inactive=inactive_users)
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # نسخة احتياطية للبيانات
    if data == "backup_data":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        # حساب عدد البيانات
        total_users = len(user_database)
        total_emails = sum(len(user['emails']) for user in user_database.values())
        
        text = "💾 " + ("نسخ احتياطي" if lang == "ar" else "Backup") + "\n\n"
        text += f"✅ البيانات محفوظة في قاعدة البيانات PostgreSQL\n\n" if lang == "ar" else f"✅ Data saved in PostgreSQL database\n\n"
        text += f"👥 المستخدمين: {total_users}\n"
        text += f"📧 الإيميلات: {total_emails}\n\n"
        text += ("✨ البيانات محمية تلقائياً!" if lang == "ar" else "✨ Data is automatically protected!")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # معلومات البوت
    if data == "bot_info":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        text = get_text(lang, "bot_info")
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # الإذاعة (رسالة جماعية)
    if data == "broadcast":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        text = get_text(lang, "broadcast_prompt")
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # الإعدادات
    if data == "admin_settings":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        # رسالة بسيطة للإعدادات
        text = "⚙️ " + ("الإعدادات" if lang == "ar" else "Settings") + "\n\n" + ("قريباً..." if lang == "ar" else "Coming soon...")
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قسم الإحصائيات
    if data == "section_stats":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        total_users = len(user_database)
        total_emails = sum(len(user['emails']) for user in user_database.values())
        active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
        
        text = f"📊 قسم الإحصائيات\n\n" if lang == "ar" else f"📊 Statistics Section\n\n"
        text += f"👥 إجمالي المستخدمين: {total_users}\n"
        text += f"📧 إجمالي الإيميلات: {total_emails}\n"
        text += f"🔄 المستخدمون النشطون: {active_users}\n"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قسم الإذاعة
    if data == "section_broadcast":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        text = "📢 " + ("قسم الإذاعة" if lang == "ar" else "Broadcasting Section") + "\n\n"
        text += ("اختر نوع الإذاعة:" if lang == "ar" else "Choose broadcast type:")
        
        keyboard = [
            [InlineKeyboardButton("📨 إذاعة للكل" if lang == "ar" else "📨 Broadcast to All", callback_data="broadcast_all")],
            [InlineKeyboardButton("👥 إذاعة للنشطين فقط" if lang == "ar" else "👥 Active Users Only", callback_data="broadcast_active")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # إذاعة للكل
    if data == "broadcast_all":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        context.user_data['waiting_for'] = 'broadcast_all'
        text = "📢 " + ("إذاعة للكل" if lang == "ar" else "Broadcast to All") + "\n\n"
        text += ("أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:" if lang == "ar" else "Send the message you want to broadcast to all users:")
        text += "\n\n⚠️ " + ("سيتم إرسالها لـ " if lang == "ar" else "Will be sent to ") + f"{len(user_database)} " + ("مستخدم" if lang == "ar" else "users")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # إذاعة للنشطين فقط
    if data == "broadcast_active":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        context.user_data['waiting_for'] = 'broadcast_active'
        active_count = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
        
        text = "📢 " + ("إذاعة للنشطين" if lang == "ar" else "Broadcast to Active Users") + "\n\n"
        text += ("أرسل الرسالة التي تريد إرسالها للمستخدمين النشطين:" if lang == "ar" else "Send the message you want to broadcast to active users:")
        text += "\n\n👥 " + ("المستخدمون النشطون: " if lang == "ar" else "Active users: ") + f"{active_count}"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قسم توجيه الرسائل
    if data == "section_forward":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        status = "✅ مفعّل" if forwarding_enabled else "❌ معطّل"
        status_en = "✅ Enabled" if forwarding_enabled else "❌ Disabled"
        
        text = "📨 " + ("قسم توجيه الرسائل" if lang == "ar" else "Message Forwarding") + "\n\n"
        text += ("الحالة: " if lang == "ar" else "Status: ") + (status if lang == "ar" else status_en) + "\n\n"
        text += ("عند التفعيل، أي رسالة يرسلها المستخدمون ستصلك مباشرة." if lang == "ar" else "When enabled, any message from users will be forwarded to you.")
        
        keyboard = [
            [InlineKeyboardButton("✅ تفعيل التوجيه" if lang == "ar" else "✅ Enable Forwarding", callback_data="forward_on")],
            [InlineKeyboardButton("❌ تعطيل التوجيه" if lang == "ar" else "❌ Disable Forwarding", callback_data="forward_off")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # تفعيل توجيه الرسائل
    if data == "forward_on":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        forwarding_enabled = True
        text = "✅ " + ("تم تفعيل توجيه الرسائل!" if lang == "ar" else "Message forwarding enabled!") + "\n\n"
        text += ("الآن أي رسالة يرسلها المستخدمون ستصلك مباشرة." if lang == "ar" else "Now any message from users will be forwarded to you.")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # تعطيل توجيه الرسائل
    if data == "forward_off":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        forwarding_enabled = False
        text = "❌ " + ("تم تعطيل توجيه الرسائل!" if lang == "ar" else "Message forwarding disabled!") + "\n\n"
        text += ("لن يتم توجيه رسائل المستخدمين إليك." if lang == "ar" else "User messages will no longer be forwarded to you.")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قسم الإعدادات
    if data == "section_settings":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        status_icon = "✅" if bot_active else "❌"
        status_text = "يعمل" if bot_active else "متوقف"
        
        text = "⚙️ " + ("الإعدادات" if lang == "ar" else "Settings") + "\n\n"
        text += f"• حالة البوت: {status_icon} {status_text}\n"
        if not bot_active and bot_offline_message:
            text += f"• رسالة الإيقاف: {bot_offline_message[:50]}...\n"
        
        keyboard = [
            [InlineKeyboardButton(f"🔄 حالة البوت: {status_icon}" if lang == "ar" else f"🔄 Bot Status: {status_icon}", callback_data="toggle_bot_status")],
            [InlineKeyboardButton("✏️ رسالة الإيقاف" if lang == "ar" else "✏️ Offline Message", callback_data="set_offline_message")],
            [InlineKeyboardButton("🔔 الإشعارات" if lang == "ar" else "🔔 Notifications", callback_data="notifications")],
            [InlineKeyboardButton("💾 نسخ احتياطي" if lang == "ar" else "💾 Backup", callback_data="backup_data")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # تبديل حالة البوت
    if data == "toggle_bot_status":
        if not is_admin(user_id):
            return
        
        bot_active = not bot_active
        
        if bot_active:
            text = "✅ " + ("تم تشغيل البوت!" if lang == "ar" else "Bot is now ACTIVE!")
            text += "\n\n" + ("البوت يعمل الآن ويمكن للمستخدمين استخدامه." if lang == "ar" else "Users can now use the bot.")
        else:
            text = "❌ " + ("تم إيقاف البوت!" if lang == "ar" else "Bot is now OFFLINE!")
            text += "\n\n" + ("البوت متوقف الآن. المستخدمون سيرون رسالة الإيقاف." if lang == "ar" else "Users will see the offline message.")
            if bot_offline_message:
                text += f"\n\n📝 رسالة الإيقاف:\n{bot_offline_message}"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # تعيين رسالة الإيقاف
    if data == "set_offline_message":
        if not is_admin(user_id):
            return
        
        context.user_data['waiting_for'] = 'offline_message'
        text = "✏️ " + ("رسالة الإيقاف" if lang == "ar" else "Offline Message") + "\n\n"
        text += ("أرسل الرسالة التي ستظهر للمستخدمين عندما يكون البوت متوقفاً:" if lang == "ar" else "Send the message users will see when the bot is offline:")
        if bot_offline_message:
            text += f"\n\n📝 الرسالة الحالية:\n{bot_offline_message}"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قسم إدارة المشرفين (للمشرف الرئيسي فقط)
    if data == "section_admins":
        if not is_admin(user_id):
            try:
                await query.answer("هذا القسم للمشرف الرئيسي فقط!", show_alert=True)
            except Exception:
                pass
            return
        
        admins = get_all_admins()
        text = "👮 " + ("إدارة المشرفين" if lang == "ar" else "Admin Management") + "\n"
        text += "━━━━━━━━━━━━━━━\n\n"
        
        text += f"👑 المشرف الرئيسي: <code>{ADMIN_ID}</code>\n\n"
        
        if admins:
            text += f"👮 المشرفون الإضافيون ({len(admins)}):\n"
            for admin in admins:
                name = admin.get('first_name') or 'مجهول'
                username = f"@{admin['username']}" if admin.get('username') else "—"
                text += f"• {name} | {username}\n"
                text += f"  ID: <code>{admin['telegram_id']}</code>\n"
        else:
            text += "لا يوجد مشرفون إضافيون حالياً\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مشرف" if lang == "ar" else "➕ Add Admin", callback_data="add_admin")],
            [InlineKeyboardButton("➖ إزالة مشرف" if lang == "ar" else "➖ Remove Admin", callback_data="remove_admin")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # إضافة مشرف جديد
    if data == "add_admin":
        if not is_admin(user_id):
            return
        
        context.user_data['waiting_for'] = 'add_admin'
        text = "➕ " + ("إضافة مشرف جديد" if lang == "ar" else "Add New Admin") + "\n\n"
        text += ("أرسل ID المستخدم أو اسم المستخدم (@username) الذي تريد إضافته كمشرف:" if lang == "ar" else "Send the user ID or @username to add as admin:")
        text += "\n\n💡 " + ("يجب أن يكون المستخدم قد استخدم البوت مسبقاً" if lang == "ar" else "User must have used the bot before")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # إزالة مشرف
    if data == "remove_admin":
        if not is_admin(user_id):
            return
        
        admins = get_all_admins()
        if not admins:
            text = "❌ لا يوجد مشرفون للإزالة"
            keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        text = "➖ " + ("إزالة مشرف" if lang == "ar" else "Remove Admin") + "\n\n"
        text += ("اختر المشرف الذي تريد إزالته:" if lang == "ar" else "Choose admin to remove:")
        
        keyboard = []
        for admin in admins:
            name = admin.get('first_name') or str(admin['telegram_id'])
            keyboard.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"confirm_remove_admin_{admin['telegram_id']}")])
        
        keyboard.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # تأكيد إزالة مشرف
    if data.startswith("confirm_remove_admin_"):
        if not is_admin(user_id):
            return
        
        admin_id = int(data.replace("confirm_remove_admin_", ""))
        
        if remove_admin(admin_id):
            text = "✅ " + ("تم إزالة المشرف بنجاح!" if lang == "ar" else "Admin removed successfully!")
        else:
            text = "❌ " + ("فشل في إزالة المشرف" if lang == "ar" else "Failed to remove admin")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قسم إدارة الأعضاء
    if data == "section_members":
        if not is_admin(user_id):
            try:
                await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            except Exception:
                pass
            return
        
        total_users = len(user_database)
        active_users = sum(1 for user in user_database.values() if len(user.get('emails', [])) > 0)
        inactive_users = total_users - active_users
        total_emails = sum(len(user.get('emails', [])) for user in user_database.values())
        
        text = "👥 " + ("إدارة الأعضاء" if lang == "ar" else "Member Management") + "\n\n"
        text += f"• إجمالي الأعضاء: {total_users}\n"
        text += f"• الأعضاء النشطون: {active_users}\n"
        text += f"• الأعضاء غير النشطين: {inactive_users}\n"
        text += f"• إجمالي الإيميلات: {total_emails}\n"
        
        keyboard = [
            [InlineKeyboardButton("📋 قائمة كل الأعضاء" if lang == "ar" else "📋 All Members", callback_data="users_list_all")],
            [InlineKeyboardButton("✅ الأعضاء النشطين" if lang == "ar" else "✅ Active Members", callback_data="users_list_active")],
            [InlineKeyboardButton("🏆 الأكثر إيميلات" if lang == "ar" else "🏆 Top Email Users", callback_data="users_list_top")],
            [InlineKeyboardButton("🔍 بحث عن عضو" if lang == "ar" else "🔍 Search Member", callback_data="search_member")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # قائمة كل الأعضاء
    if data == "users_list_all":
        if not is_admin(user_id):
            return
        
        text = "📋 " + ("قائمة كل الأعضاء" if lang == "ar" else "All Members") + "\n"
        text += "━━━━━━━━━━━━━━━\n\n"
        
        count = 0
        for uid, info in list(user_database.items())[:20]:  # أول 20 عضو
            count += 1
            name = info.get('first_name', '') or 'مجهول'
            if info.get('last_name'):
                name += f" {info.get('last_name')}"
            username = f"@{info.get('username')}" if info.get('username') else "—"
            emails_count = len(info.get('emails', []))
            status = "✅" if emails_count > 0 else "⚪"
            
            text += f"{count}. {status} <b>{name}</b>\n"
            text += f"    🆔 {username} | 📧 {emails_count}\n"
            text += f"    ID: <code>{uid}</code>\n\n"
        
        if len(user_database) > 20:
            text += f"\n... و {len(user_database) - 20} عضو آخر"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # قائمة الأعضاء النشطين
    if data == "users_list_active":
        if not is_admin(user_id):
            return
        
        active_members = [(uid, info) for uid, info in user_database.items() if len(info.get('emails', [])) > 0]
        
        text = "✅ " + ("الأعضاء النشطين" if lang == "ar" else "Active Members") + f" ({len(active_members)})\n"
        text += "━━━━━━━━━━━━━━━\n\n"
        
        count = 0
        for uid, info in active_members[:20]:
            count += 1
            name = info.get('first_name', '') or 'مجهول'
            if info.get('last_name'):
                name += f" {info.get('last_name')}"
            username = f"@{info.get('username')}" if info.get('username') else "—"
            emails_count = len(info.get('emails', []))
            
            text += f"{count}. <b>{name}</b>\n"
            text += f"    🆔 {username} | 📧 {emails_count} إيميل\n"
            text += f"    ID: <code>{uid}</code>\n\n"
        
        if len(active_members) > 20:
            text += f"\n... و {len(active_members) - 20} عضو نشط آخر"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # الأكثر إيميلات
    if data == "users_list_top":
        if not is_admin(user_id):
            return
        
        # ترتيب حسب عدد الإيميلات
        sorted_users = sorted(
            user_database.items(), 
            key=lambda x: len(x[1].get('emails', [])), 
            reverse=True
        )[:10]
        
        text = "🏆 " + ("الأكثر إيميلات" if lang == "ar" else "Top Email Users") + "\n"
        text += "━━━━━━━━━━━━━━━\n\n"
        
        medals = ["🥇", "🥈", "🥉"]
        count = 0
        for uid, info in sorted_users:
            emails_count = len(info.get('emails', []))
            if emails_count == 0:
                continue
            
            count += 1
            medal = medals[count-1] if count <= 3 else f"{count}."
            name = info.get('first_name', '') or 'مجهول'
            if info.get('last_name'):
                name += f" {info.get('last_name')}"
            username = f"@{info.get('username')}" if info.get('username') else "—"
            
            text += f"{medal} <b>{name}</b>\n"
            text += f"    🆔 {username}\n"
            text += f"    📧 {emails_count} إيميل\n"
            text += f"    ID: <code>{uid}</code>\n\n"
        
        if count == 0:
            text += "لا يوجد أعضاء لديهم إيميلات"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # بحث عن عضو
    if data == "search_member":
        if not is_admin(user_id):
            return
        
        context.user_data['waiting_for'] = 'search_member'
        text = "🔍 " + ("بحث عن عضو" if lang == "ar" else "Search Member") + "\n\n"
        text += ("أرسل اسم المستخدم أو ID أو اليوزرنيم للبحث:" if lang == "ar" else "Send username, ID, or name to search:")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

# ============= معالج الرسائل النصية =============

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل النصية"""
    global forwarding_enabled
    user_id = update.effective_user.id
    lang = get_user_language(user_id) or "ar"
    
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
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=forward_text,
                parse_mode='HTML'
            )
        except Exception as e:
            print(f"❌ فشل توجيه الرسالة للأدمن: {e}")
    
    # التحقق من أن المستخدم في انتظار إدخال
    waiting_for = context.user_data.get('waiting_for')
    
    if not waiting_for:
        return
    
    # تعيين اسم القناة
    if waiting_for == 'channel_username' and user_id == ADMIN_ID:
        channel_username = update.message.text.strip().replace('@', '')
        
        print(f"🔍 المشرف يحاول تعيين القناة: @{channel_username}")
        
        # فحص صحة القناة أولاً
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            print(f"✅ القناة @{channel_username} موجودة: {chat.title}")
            
            # حفظ القناة في قاعدة البيانات (مع المعرّف والعنوان)
            if set_channel(channel_username, chat.id, chat.title):
                text = get_text(lang, "channel_set_success", channel=channel_username)
                text += f"\n\n📢 اسم القناة: <b>{chat.title}</b>"
                text += f"\n🆔 معرّف القناة: <code>{chat.id}</code>"
            else:
                text = get_text(lang, "error")
                
        except Exception as e:
            print(f"❌ خطأ في الوصول للقناة @{channel_username}: {e}")
            error_msg = str(e).lower()
            
            if 'not found' in error_msg or 'chat not found' in error_msg:
                text = "❌ القناة غير موجودة\n\nتأكد من:\n• اسم القناة صحيح\n• القناة عامة (public)\n• لم تحذف القناة"
            elif 'invalid' in error_msg:
                text = "❌ اسم القناة غير صحيح\n\nأدخل اسم القناة بدون @"
            else:
                text = f"❌ خطأ في الوصول للقناة\n\n{str(e)[:200]}"
        
        context.user_data['waiting_for'] = None
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # تعيين رسالة الاشتراك
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
    
    # إرسال إذاعة للكل
    if waiting_for == 'broadcast_all' and user_id == ADMIN_ID:
        broadcast_message = update.message.text
        context.user_data['waiting_for'] = None
        
        # إرسال رسالة انتظار
        wait_text = "⏳ " + ("جاري إرسال الإذاعة..." if lang == "ar" else "Broadcasting message...")
        wait_msg = await update.message.reply_text(wait_text)
        
        # إرسال الرسالة لجميع المستخدمين
        success_count = 0
        fail_count = 0
        
        for uid in user_database.keys():
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"📢 {'رسالة من الإدارة' if lang == 'ar' else 'Message from Admin'}:\n\n{broadcast_message}"
                )
                success_count += 1
            except Exception as e:
                print(f"❌ فشل إرسال الرسالة للمستخدم {uid}: {e}")
                fail_count += 1
        
        # حذف رسالة الانتظار
        try:
            await wait_msg.delete()
        except:
            pass
        
        # إرسال النتيجة
        if lang == "ar":
            result_text = f"✅ تم إرسال الإذاعة بنجاح!\n\n"
            result_text += f"📨 نجح الإرسال: {success_count}\n"
            result_text += f"❌ فشل الإرسال: {fail_count}"
        else:
            result_text = f"✅ Broadcast sent successfully!\n\n"
            result_text += f"📨 Sent: {success_count}\n"
            result_text += f"❌ Failed: {fail_count}"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]
        await update.message.reply_text(result_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # إرسال إذاعة للنشطين فقط
    if waiting_for == 'broadcast_active' and user_id == ADMIN_ID:
        broadcast_message = update.message.text
        context.user_data['waiting_for'] = None
        
        # إرسال رسالة انتظار
        wait_text = "⏳ " + ("جاري إرسال الإذاعة للنشطين..." if lang == "ar" else "Broadcasting to active users...")
        wait_msg = await update.message.reply_text(wait_text)
        
        # إرسال الرسالة للمستخدمين النشطين فقط (من لديهم إيميلات)
        success_count = 0
        fail_count = 0
        
        for uid, user_info in user_database.items():
            # فقط المستخدمين الذين لديهم إيميلات
            if len(user_info.get('emails', [])) > 0:
                try:
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=f"📢 {'رسالة من الإدارة' if lang == 'ar' else 'Message from Admin'}:\n\n{broadcast_message}"
                    )
                    success_count += 1
                except Exception as e:
                    print(f"❌ فشل إرسال الرسالة للمستخدم {uid}: {e}")
                    fail_count += 1
        
        # حذف رسالة الانتظار
        try:
            await wait_msg.delete()
        except:
            pass
        
        # إرسال النتيجة
        if lang == "ar":
            result_text = f"✅ تم إرسال الإذاعة للنشطين بنجاح!\n\n"
            result_text += f"📨 نجح الإرسال: {success_count}\n"
            result_text += f"❌ فشل الإرسال: {fail_count}"
        else:
            result_text = f"✅ Broadcast to active users sent!\n\n"
            result_text += f"📨 Sent: {success_count}\n"
            result_text += f"❌ Failed: {fail_count}"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_broadcast")]]
        await update.message.reply_text(result_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # البحث عن عضو
    if waiting_for == 'search_member' and user_id == ADMIN_ID:
        search_query = update.message.text.strip().lower()
        context.user_data['waiting_for'] = None
        
        results = []
        for uid, info in user_database.items():
            # البحث في ID
            if search_query in uid:
                results.append((uid, info))
                continue
            # البحث في الاسم
            name = f"{info.get('first_name', '')} {info.get('last_name', '')}".lower()
            if search_query in name:
                results.append((uid, info))
                continue
            # البحث في اليوزرنيم
            username = info.get('username', '').lower()
            if search_query in username:
                results.append((uid, info))
                continue
        
        if results:
            text = "🔍 " + f"نتائج البحث عن '{search_query}':\n"
            text += "━━━━━━━━━━━━━━━\n\n"
            
            for uid, info in results[:10]:
                name = info.get('first_name', '') or 'مجهول'
                if info.get('last_name'):
                    name += f" {info.get('last_name')}"
                username = f"@{info.get('username')}" if info.get('username') else "—"
                emails_count = len(info.get('emails', []))
                status = "✅ نشط" if emails_count > 0 else "⚪ غير نشط"
                
                text += f"👤 <b>{name}</b>\n"
                text += f"🆔 {username}\n"
                text += f"📧 {emails_count} إيميل | {status}\n"
                text += f"🔢 ID: <code>{uid}</code>\n\n"
            
            if len(results) > 10:
                text += f"\n... و {len(results) - 10} نتيجة أخرى"
        else:
            text = "❌ لم يتم العثور على أي عضو بهذا البحث"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_members")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    # رسالة الإيقاف
    if waiting_for == 'offline_message' and user_id == ADMIN_ID:
        global bot_offline_message
        bot_offline_message = update.message.text.strip()
        context.user_data['waiting_for'] = None
        
        text = "✅ " + ("تم حفظ رسالة الإيقاف!" if lang == "ar" else "Offline message saved!") + "\n\n"
        text += f"📝 الرسالة:\n{bot_offline_message}"
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # إضافة مشرف جديد
    if waiting_for == 'add_admin' and user_id == ADMIN_ID:
        search_query = update.message.text.strip()
        context.user_data['waiting_for'] = None
        
        # البحث عن المستخدم
        found_user = find_user_by_username_or_id(search_query)
        
        if found_user:
            # التحقق من أنه ليس مشرفاً بالفعل
            if is_admin(found_user['telegram_id']):
                text = "⚠️ " + ("هذا المستخدم مشرف بالفعل!" if lang == "ar" else "This user is already an admin!")
            else:
                # إضافة المشرف
                if add_admin(found_user['telegram_id'], found_user.get('username'), found_user.get('first_name'), user_id):
                    name = found_user.get('first_name') or str(found_user['telegram_id'])
                    username = f"@{found_user['username']}" if found_user.get('username') else ""
                    
                    text = "✅ " + ("تم إضافة المشرف بنجاح!" if lang == "ar" else "Admin added successfully!") + "\n\n"
                    text += f"👮 {name} {username}\n"
                    text += f"🆔 ID: <code>{found_user['telegram_id']}</code>"
                else:
                    text = "❌ " + ("فشل في إضافة المشرف" if lang == "ar" else "Failed to add admin")
        else:
            text = "❌ " + ("لم يتم العثور على المستخدم!" if lang == "ar" else "User not found!") + "\n\n"
            text += ("تأكد من أن المستخدم قد استخدم البوت مسبقاً" if lang == "ar" else "Make sure the user has used the bot before")
        
        keyboard = [[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return

# ============= تشغيل البوت =============

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء العام للبوت"""
    import traceback
    
    # تسجيل الخطأ
    print(f"⚠️ حدث خطأ: {context.error}")
    
    # تجاهل الأخطاء المعروفة التي لا تؤثر على عمل البوت
    error_message = str(context.error)
    
    # قائمة الأخطاء التي يمكن تجاهلها
    ignorable_errors = [
        "Query is too old",
        "query id is invalid",
        "Message is not modified",
    ]
    
    # التحقق من الأخطاء التي يمكن تجاهلها
    for ignorable in ignorable_errors:
        if ignorable in error_message:
            return  # تجاهل الخطأ
    
    # طباعة تفاصيل الخطأ للأخطاء المهمة
    print("❌ خطأ غير متوقع:")
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

def main():
    """تشغيل البوت"""
    # احصل على توكن البوت من متغيرات البيئة
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        print("❌ خطأ: يرجى تعيين TELEGRAM_BOT_TOKEN في متغيرات البيئة")
        print("❌ Error: Please set TELEGRAM_BOT_TOKEN in environment variables")
        return
    
    # إنشاء التطبيق
    application = Application.builder().token(token).build()
    
    # إضافة المعالجات
    from telegram.ext import MessageHandler, filters
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # إضافة معالج الأخطاء
    application.add_error_handler(error_handler)
    
    # بدء البوت
    print("🤖 البوت يعمل الآن...")
    print("🤖 Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

================================================================================
                              نهاية الملفات
================================================================================
