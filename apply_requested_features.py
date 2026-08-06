from pathlib import Path
import ast

path = Path("telegram_bot.py")
source = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match, found {count}: {old[:100]!r}")
    source = source.replace(old, new, 1)


def replace_between(start_marker: str, end_marker: str, replacement: str) -> None:
    global source
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start == -1 or end == -1:
        raise RuntimeError(f"Markers not found: {start_marker!r} -> {end_marker!r}")
    source = source[:start] + replacement + source[end:]


replace_once(
    "DEFAULT_EMAIL_LIMIT = 0\n",
    "DEFAULT_EMAIL_LIMIT = 0\nMEMBERS_PAGE_SIZE = 10\nBOT_STARTED_AT = time.time()\n",
)

replace_once(
    "def normalize_telegram_username(value: str) -> str:\n",
    '''def get_member_email_limit(user_id: int):
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
''',
)

old_admin = '''def get_admin_panel_keyboard(_lang, user_id):
    keyboard = [
        [InlineKeyboardButton("📊 قسم الإحصائيات", callback_data="section_stats", style="primary")],
        [InlineKeyboardButton("📢 قسم الإذاعة", callback_data="section_broadcast", style="primary")],
        [InlineKeyboardButton("📨 قسم توجيه الرسائل", callback_data="section_forward", style="primary")],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="channel_management", style="primary")],
        [InlineKeyboardButton("⚙️ الإعدادات", callback_data="section_settings", style="primary")],
        [InlineKeyboardButton("👥 إدارة الأعضاء", callback_data="section_members", style="primary")],
        [InlineKeyboardButton("🔢 حد إنشاء الإيميلات", callback_data="section_email_limit", style="primary")],
        [InlineKeyboardButton("🌐 إدارة الدومينات المدفوعة", callback_data="section_paid_domains", style="primary")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮 إدارة المشرفين", callback_data="section_admins", style="primary")])

    keyboard.extend([
        [InlineKeyboardButton("🛑 الحظر / فك الحظر", callback_data="section_ban", style="danger")],
        [InlineKeyboardButton("👋 رسالة الترحيب", callback_data="section_welcome", style="success")],
        [InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info")],
        [InlineKeyboardButton(get_text("ar", "btn_back"), callback_data="back_to_menu")],
    ])
    return InlineKeyboardMarkup(keyboard)


'''
new_admin = '''def get_admin_panel_keyboard(_lang, user_id):
    keyboard = [
        [InlineKeyboardButton("📊 قسم الإحصائيات", callback_data="section_stats", style="primary")],
        [InlineKeyboardButton("📢 قسم الإذاعة", callback_data="section_broadcast", style="primary")],
        [InlineKeyboardButton("📨 قسم توجيه الرسائل", callback_data="section_forward", style="primary")],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="channel_management", style="primary")],
        [InlineKeyboardButton("⚙️ الإعدادات", callback_data="section_settings", style="primary")],
        [InlineKeyboardButton("👥 إدارة الأعضاء", callback_data="section_members", style="primary")],
        [InlineKeyboardButton("🔢 حد إنشاء الإيميلات", callback_data="section_email_limit", style="primary")],
        [InlineKeyboardButton("🌐 إدارة الدومينات المدفوعة", callback_data="section_paid_domains", style="primary")],
        [InlineKeyboardButton("🩺 حالة البوت والخدمات", callback_data="section_health", style="primary")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮 إدارة المشرفين", callback_data="section_admins", style="primary")])

    keyboard.extend([
        [InlineKeyboardButton("🛑 الحظر / فك الحظر", callback_data="section_ban", style="danger")],
        [InlineKeyboardButton("👋 رسالة الترحيب", callback_data="section_welcome", style="success")],
        [InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info")],
        [InlineKeyboardButton(get_text("ar", "btn_back"), callback_data="back_to_menu")],
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


'''
replace_once(old_admin, new_admin)

replace_once("        email_limit = get_email_limit()\n", "        email_limit = get_effective_email_limit(user_id)\n")

replace_once(
    '    if data == "channel_management":\n',
    '''    if data == "section_health":
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
''',
)

new_limit_section = '''    if data == "section_email_limit":
        if not is_admin(user_id):
            return
        limit = get_email_limit()
        current = "غير محدود" if limit == 0 else str(limit)
        contact_username = get_admin_contact_username()
        contact_text = f"@{contact_username}" if contact_username else "غير محدد"
        rows = [
            [InlineKeyboardButton("✏️ تحديد العدد", callback_data="set_email_limit", style="primary")],
        ]
        if user_id == ADMIN_ID:
            rows.append([
                InlineKeyboardButton(
                    "🎯 تحديد حد عضو عبر ID",
                    callback_data="set_member_email_limit",
                    style="primary",
                )
            ])
        rows.extend([
            [InlineKeyboardButton("👤 إضافة يوزر التواصل", callback_data="set_admin_contact_username", transparent=True)],
            [InlineKeyboardButton("♾️ إلغاء الحد", callback_data="clear_email_limit", style="danger")],
            [InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="admin_panel")],
        ])
        await query.edit_message_text(
            "🔢 حد إنشاء الإيميلات\n\n"
            f"الحد الحالي لكل مستخدم: {current}\n"
            f"يوزر التواصل مع الأدمن: {contact_text}",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

'''
replace_between(
    '    if data == "section_email_limit":\n',
    '    if data == "set_email_limit":\n',
    new_limit_section,
)

replace_once(
    '    if data == "set_admin_contact_username":\n',
    '''    if data == "set_member_email_limit":
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
''',
)

paginated_callbacks = '''    if data == "users_list_all" or re.fullmatch(r"users_list_all_\d+", data):
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

'''
replace_between(
    '    if data == "users_list_all":\n',
    '    if data == "search_member":\n',
    paginated_callbacks,
)

replace_once(
    '    # تحديد يوزر التواصل مع الأدمن - للمشرف الرئيسي فقط\n',
    '''    # اختيار العضو عبر ID لتحديد حد خاص له - للمشرف الرئيسي فقط
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
''',
)

ast.parse(source)
for marker in (
    "section_health",
    "users_list_all_",
    "member_email_limit_id",
    "get_effective_email_limit(user_id)",
):
    if marker not in source:
        raise RuntimeError(f"Missing marker: {marker}")

path.write_text(source, encoding="utf-8")
