from pathlib import Path

SOURCE_PATH = Path("telegram_bot.py")
EXPORT_PATH = Path("bot_code_export.txt")

text = SOURCE_PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, found {count}")
    text = text.replace(old, new, 1)


# 1) كلمة مرور ثابتة لكل الإيميلات الجديدة.
replace_once(
    'LEGACY_MAIL_PASSWORD = os.getenv("MAIL_TM_LEGACY_PASSWORD", "TempMail123")\n',
    'LEGACY_MAIL_PASSWORD = os.getenv("MAIL_TM_LEGACY_PASSWORD", "TempMail123")\nFIXED_MAIL_PASSWORD = "TempMail123"\n',
    "fixed mail password constant",
)

password_chars_line = '        password_chars = string.ascii_letters + string.digits\n'
if text.count(password_chars_line) != 2:
    raise SystemExit(f"password chars lines: expected 2, found {text.count(password_chars_line)}")
text = text.replace(password_chars_line, "", 2)

random_password_line = '            password = "".join(secrets.choice(password_chars) for _ in range(20))\n'
if text.count(random_password_line) != 2:
    raise SystemExit(f"random password assignments: expected 2, found {text.count(random_password_line)}")
text = text.replace(random_password_line, '            password = FIXED_MAIL_PASSWORD\n', 2)

# 2) تحميل حالة التوجيه من قاعدة البيانات بعد تهيئتها.
replace_once(
    'init_database()\nuser_database = load_user_data()\n',
    'init_database()\nforwarding_enabled = get_setting("forwarding_enabled", "0") == "1"\nuser_database = load_user_data()\n',
    "load forwarding state",
)

# 3) إزالة زر الدومينات المدفوعة من القائمة الرئيسية فقط.
replace_once(
    '        [InlineKeyboardButton("🌐 تغيير الدومين", callback_data="change_domain", style="primary")],\n',
    '',
    "remove paid domains from main menu",
)

# 4) نقل الدومينات المدفوعة إلى شاشة إنشاء إيميل جديد مع شرح واضح.
old_create_text = '''        text = (
            "✨ إنشاء إيميل جديد\\n\\n"
            "من هنا يمكنك اختيار طريقة إنشاء بريدك الإلكتروني.\\n\\n"
            "🎲 الإنشاء السريع:\\n"
            "ينشئ لك البوت إيميل جديد مباشرة ويختار أحد\\n"
            "الدومينات المجانية المتاحة تلقائياً.\\n\\n"
            "🌐 اختيار الدومين:\\n"
            "اختر بنفسك أحد الدومينات المجانية المتاحة\\n"
            "لإنشاء الإيميل عليه."
        )
'''
new_create_text = '''        text = (
            "✨ إنشاء إيميل جديد\\n\\n"
            "من هنا يمكنك اختيار طريقة إنشاء بريدك الإلكتروني.\\n\\n"
            "🎲 الإنشاء السريع:\\n"
            "ينشئ لك البوت إيميل جديد مباشرة ويختار أحد\\n"
            "الدومينات المجانية المتاحة تلقائياً.\\n\\n"
            "🌐 اختيار الدومين:\\n"
            "اختر بنفسك أحد الدومينات المجانية المتاحة\\n"
            "لإنشاء الإيميل عليه.\\n\\n"
            "💎 الدومينات المدفوعة:\\n"
            "استعرض الدومينات المدفوعة المتوفرة واختر\\n"
            "الدومين الذي ترغب باستخدامه."
        )
'''
replace_once(old_create_text, new_create_text, "create email explanation")

old_create_keyboard = '''                ],
                [InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="back_to_menu",
                    style="primary",
                )],
'''
new_create_keyboard = '''                ],
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
'''
# هذا النمط قد يظهر في أماكن أخرى، لذا نطبقه فقط داخل مقطع إنشاء الإيميل.
create_start = text.find('    # إنشاء إيميل: يختار المستخدم بين الإنشاء السريع أو دومين مجاني محدد.\n')
create_end = text.find('    if data == "create_email_fast":\n', create_start)
if create_start == -1 or create_end == -1:
    raise SystemExit("create email block bounds not found")
create_block = text[create_start:create_end]
if create_block.count(old_create_keyboard) != 1:
    raise SystemExit(f"create email keyboard anchor: expected 1, found {create_block.count(old_create_keyboard)}")
create_block = create_block.replace(old_create_keyboard, new_create_keyboard, 1)
text = text[:create_start] + create_block + text[create_end:]

# 5) شاشة الدومينات المدفوعة ترجع إلى قسم إنشاء الإيميل وتستخدم الاسم الجديد.
replace_once(
    '                "🌐 لا توجد دومينات مدفوعة متاحة حالياً.",\n',
    '                "💎 لا توجد دومينات مدفوعة متاحة حالياً.",\n',
    "paid domains empty text",
)

# يوجد رجوعان للقائمة الرئيسية داخل بلوك change_domain: حالة الفراغ وأسفل القائمة.
change_start = text.find('    # عرض الدومينات الشكلية المدفوعة للمستخدم\n    if data == "change_domain":\n')
paid_detail_start = text.find('    if re.fullmatch(r"paid_domain_\\d+", data):\n', change_start)
if change_start == -1 or paid_detail_start == -1:
    raise SystemExit("paid domains block bounds not found")
change_block = text[change_start:paid_detail_start]
back_old = 'callback_data="back_to_menu"'
if change_block.count(back_old) != 2:
    raise SystemExit(f"paid domains back callbacks: expected 2, found {change_block.count(back_old)}")
change_block = change_block.replace(back_old, 'callback_data="create_email"', 2)
old_paid_prompt = '            "🌐 اختر الدومين الذي تريد استخدامه:",\n'
new_paid_prompt = '            "💎 الدومينات المدفوعة\\n\\nاختر أحد الدومينات المدفوعة المتاحة:",\n'
if change_block.count(old_paid_prompt) != 1:
    raise SystemExit("paid domains prompt anchor not found")
change_block = change_block.replace(old_paid_prompt, new_paid_prompt, 1)
text = text[:change_start] + change_block + text[paid_detail_start:]

# 6) كلمة المرور لا تظهر في شاشة المشرف عند مشاهدة إيميل عضو.
old_admin_detail = '''        email_data = emails[email_index]
        address = str(email_data.get("address") or "غير معروف")
        email_password = email_data.get("password") or LEGACY_MAIL_PASSWORD
        text = (
            "📧 بيانات إيميل العضو\\n\\n"
            f"🔢 ID العضو: <code>{target_id}</code>\\n"
            f"📧 الإيميل: <code>{telegram_html(address)}</code>\\n"
            f"🔑 كلمة المرور: <code>{telegram_html(email_password)}</code>"
        )
'''
new_admin_detail = '''        email_data = emails[email_index]
        address = str(email_data.get("address") or "غير معروف")
        text = (
            "📧 بيانات إيميل العضو\\n\\n"
            f"🔢 ID العضو: <code>{target_id}</code>\\n"
            f"📧 الإيميل: <code>{telegram_html(address)}</code>"
        )
'''
replace_once(old_admin_detail, new_admin_detail, "hide password from admin member viewer")

# 7) حفظ تفعيل/تعطيل التوجيه في bot_settings.
old_forward_on = '''    if data == "forward_on":
        if not is_admin(user_id):
            return
        forwarding_enabled = True
        await query.edit_message_text("✅ تم تفعيل توجيه الرسائل!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]))
        return
'''
new_forward_on = '''    if data == "forward_on":
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
'''
replace_once(old_forward_on, new_forward_on, "persist forward on")

old_forward_off = '''    if data == "forward_off":
        if not is_admin(user_id):
            return
        forwarding_enabled = False
        await query.edit_message_text("❌ تم تعطيل توجيه الرسائل!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_forward")]]))
        return
'''
new_forward_off = '''    if data == "forward_off":
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
'''
replace_once(old_forward_off, new_forward_off, "persist forward off")

# فحوصات موجهة قبل الكتابة.
if text.count('password = FIXED_MAIL_PASSWORD') != 2:
    raise SystemExit("fixed password is not used by both creation paths")
if '🔑 كلمة المرور:' in text:
    raise SystemExit("admin password label still exists")
if 'text = f"📧 <code>{email_data[\'address\']}</code>\\n🔑 <code>{telegram_html(email_password)}</code>"' not in text:
    raise SystemExit("user own email password view was unexpectedly changed")
if 'forwarding_enabled = get_setting("forwarding_enabled", "0") == "1"' not in text:
    raise SystemExit("forwarding state is not loaded from settings")
if text.count('set_setting("forwarding_enabled", "1")') != 1 or text.count('set_setting("forwarding_enabled", "0")') != 1:
    raise SystemExit("forwarding state persistence callbacks are incomplete")
if '[InlineKeyboardButton("🌐 تغيير الدومين", callback_data="change_domain", style="primary")]' in text:
    raise SystemExit("old paid-domain main menu button still exists")
if '"💎 الدومينات المدفوعة",\n                    callback_data="change_domain"' not in text:
    raise SystemExit("paid-domain option was not added to create-email screen")

compile(text, "telegram_bot.py", "exec")
SOURCE_PATH.write_text(text, encoding="utf-8")

# مزامنة نسخة التصدير مع telegram_bot.py مع الحفاظ على مقدمتها الحالية.
export_text = EXPORT_PATH.read_text(encoding="utf-8")
source_marker = "#!/usr/bin/env python3"
marker_index = export_text.find(source_marker)
if marker_index == -1:
    raise SystemExit("bot_code_export source marker not found")
EXPORT_PATH.write_text(export_text[:marker_index] + text, encoding="utf-8")

print("Requested changes applied and export synchronized.")
