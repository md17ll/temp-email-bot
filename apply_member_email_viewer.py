from pathlib import Path

path = Path("telegram_bot.py")
old_source = path.read_text(encoding="utf-8")
source = old_source


def replace_once(old: str, new: str, label: str):
    global source
    if old not in source:
        raise SystemExit(f"missing replacement target: {label}")
    source = source.replace(old, new, 1)


def insert_before(marker: str, block: str, label: str):
    global source
    if marker not in source:
        raise SystemExit(f"missing insertion marker: {label}")
    source = source.replace(marker, block + marker, 1)


# حماية فتح وارد العضو من الضغط المتكرر أيضاً.
replace_once(
'''    elif re.fullmatch(r"inbox_\\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "inbox", INBOX_COOLDOWN_SECONDS
        )
''',
'''    elif re.fullmatch(r"inbox_\\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "inbox", INBOX_COOLDOWN_SECONDS
        )
    elif re.fullmatch(r"member_inbox_\\d+_\\d+_\\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "admin_member_inbox", INBOX_COOLDOWN_SECONDS
        )
''',
"member inbox cooldown",
)

# لوحات عرض إيميلات العضو ورسائله للمشرف.
insert_before(
'''def get_channel_management_keyboard(_lang):
''',
r'''def get_admin_member_emails_view(target_id: int, requested_page: int = 0):
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


''',
"admin member email helpers",
)

# زر جديد ملوّن داخل إدارة الأعضاء.
replace_once(
'''            InlineKeyboardButton("🏆 الأكثر إيميلات", callback_data="users_list_top", style="primary"),
            InlineKeyboardButton("🔍 بحث عن عضو", callback_data="search_member", style="primary"),
''',
'''            InlineKeyboardButton("🏆 الأكثر إيميلات", callback_data="users_list_top", style="primary"),
            InlineKeyboardButton("🔍 بحث عن عضو", callback_data="search_member", style="primary"),
            InlineKeyboardButton("📧 إيميلات عضو", callback_data="member_emails", style="primary"),
''',
"member emails button",
)

# callbacks الخاصة بعرض إيميلات العضو ثم الوارد ثم الرسالة.
insert_before(
'''    if data == "search_member":
''',
r'''    if data == "member_emails":
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
        email_password = email_data.get("password") or LEGACY_MAIL_PASSWORD
        text = (
            "📧 بيانات إيميل العضو\n\n"
            f"🔢 ID العضو: <code>{target_id}</code>\n"
            f"📧 الإيميل: <code>{telegram_html(address)}</code>\n"
            f"🔑 كلمة المرور: <code>{telegram_html(email_password)}</code>"
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

''',
"member email callbacks",
)

# إدخال ID العضو من لوحة الإدارة.
insert_before(
'''    # اختيار عضو لحذف إيميلاته - للمشرف الرئيسي فقط
''',
r'''    # عرض إيميلات عضو ووارده - للمشرفين فقط
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

''',
"member email id input",
)

if source == old_source:
    raise SystemExit("no source changes applied")

path.write_text(source, encoding="utf-8")

# مزامنة ملف التصدير بدون لمس مقدمته.
export_path = Path("bot_code_export.txt")
if export_path.exists():
    export_text = export_path.read_text(encoding="utf-8")
    if export_text.endswith(old_source):
        export_path.write_text(export_text[:-len(old_source)] + source, encoding="utf-8")
    else:
        raise SystemExit("bot_code_export.txt does not end with current telegram_bot.py")

print("member email viewer patch applied")
