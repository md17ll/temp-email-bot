from pathlib import Path

path = Path("telegram_bot.py")
text = path.read_text(encoding="utf-8")

old_main = '        [InlineKeyboardButton(get_text("ar", "btn_delete_all"), callback_data="confirm_delete_all", style="danger")],'
new_main = '        [InlineKeyboardButton("⚙️ إدارة إيميلاتك", callback_data="manage_emails", style="danger")],'
if old_main not in text:
    raise SystemExit("Main-menu delete-all button anchor not found")
text = text.replace(old_main, new_main, 1)

anchor = "    # عرض تفاصيل إيميل\n"
if 'if data == "manage_emails":' in text:
    raise SystemExit("Email management callbacks already exist unexpectedly")
if anchor not in text:
    raise SystemExit("Email detail anchor not found")

block = '''    # إدارة إيميلات المستخدم من قسم واحد بعيداً عن القائمة الرئيسية
    if data == "manage_emails":
        emails = get_user_emails(user_id)
        text = (
            "⚙️ إدارة إيميلاتك\\n\\n"
            f"📧 عدد إيميلاتك الحالية: {len(emails)}\\n\\n"
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
            text += "\\n\\n📭 لا توجد إيميلات حالياً."
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

    if re.fullmatch(r"manage_confirm_delete_\\d+", data):
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
            "⚠️ تأكيد حذف الإيميل\\n\\n"
            f"📧 {address}\\n\\n"
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

    if re.fullmatch(r"manage_delete_\\d+", data):
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
            "⚠️ تأكيد حذف جميع الإيميلات\\n\\n"
            f"📧 سيتم حذف جميع إيميلاتك الحالية: {len(emails)}\\n\\n"
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

'''
text = text.replace(anchor, block + anchor, 1)
path.write_text(text, encoding="utf-8")

# فحص نحوي بدون إنشاء __pycache__.
compile(text, "telegram_bot.py", "exec")

# مزامنة ملف التصدير مع الحفاظ على مقدمته كما هي.
export_path = Path("bot_code_export.txt")
export_text = export_path.read_text(encoding="utf-8")
marker = "================================================================================\n📁 telegram_bot.py\n================================================================================\n"
if marker not in export_text:
    raise SystemExit("telegram_bot.py export marker not found")
prefix = export_text.split(marker, 1)[0] + marker
export_path.write_text(prefix + text, encoding="utf-8")

checks = [
    'InlineKeyboardButton("⚙️ إدارة إيميلاتك", callback_data="manage_emails", style="danger")',
    'callback_data="manage_delete_one"',
    'callback_data="manage_confirm_delete_all"',
    'callback_data="manage_delete_all"',
    '"✅ تأكيد الحذف"',
]
for check in checks:
    if check not in text:
        raise SystemExit(f"Missing expected change: {check}")
if old_main in text:
    raise SystemExit("Old main-menu delete-all button still present")
