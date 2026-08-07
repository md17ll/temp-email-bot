from pathlib import Path
import ast

path = Path("telegram_bot.py")
source = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match, found {count}: {old[:140]!r}")
    source = source.replace(old, new, 1)


def replace_between(start_marker: str, end_marker: str, replacement: str) -> None:
    global source
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start == -1 or end == -1:
        raise RuntimeError(f"Markers not found: {start_marker!r} -> {end_marker!r}")
    source = source[:start] + replacement + source[end:]


replace_once(
    'USER_ACTION_TIMESTAMPS = {}\n',
    'USER_ACTION_TIMESTAMPS = {}\n'
    'DEFAULT_SUBSCRIPTION_MESSAGE = (\n'
    '    "⚠️ يجب عليك الاشتراك في القنوات التالية لاستخدام البوت:\\n\\n"\n'
    '    "بعد الاشتراك في جميع القنوات اضغط: ✅ التحقق من الاشتراك"\n'
    ')\n',
)

replace_once(
    'def get_email_limit() -> int:\n',
    '''def get_global_subscription_message() -> str:\n    """رسالة اشتراك إجبارية عامة واحدة لكل القنوات."""\n    value = get_setting("global_subscription_message", DEFAULT_SUBSCRIPTION_MESSAGE).strip()\n    return value or DEFAULT_SUBSCRIPTION_MESSAGE\n\n\ndef set_global_subscription_message(message: str) -> bool:\n    value = str(message or "").strip()\n    if not value:\n        return False\n    return set_setting("global_subscription_message", value)\n\n\ndef get_email_limit() -> int:\n''',
)

replace_between(
    'def subscription_prompt(_lang: str, channels, message: str = ""):\n',
    '# ================== mail.tm API ==================\n',
    '''def subscription_prompt(_lang: str, channels, message: str = ""):\n    """عرض الرسالة العامة مع القنوات الناقصة وأزرار الانضمام."""\n    if isinstance(channels, str):\n        channels = [{\n            "channel_username": channels,\n            "channel_title": channels,\n        }]\n    channels = list(channels or [])\n\n    channel_lines = []\n    for index, channel in enumerate(channels, start=1):\n        username = str(channel.get("channel_username") or "").lstrip("@")\n        title = str(channel.get("channel_title") or username)\n        channel_lines.append(f"{index}. 📢 {title} — @{username}")\n\n    channels_text = "\\n".join(channel_lines)\n    global_message = get_global_subscription_message()\n    message_parts = global_message.split("\\n\\n", 1)\n    if len(message_parts) == 2:\n        text = f"{message_parts[0]}\\n\\n{channels_text}\\n\\n{message_parts[1]}"\n    elif channels_text:\n        text = f"{global_message}\\n\\n{channels_text}"\n    else:\n        text = global_message\n\n    rows = []\n    for channel in channels:\n        username = str(channel.get("channel_username") or "").lstrip("@")\n        title = str(channel.get("channel_title") or username)\n        display_title = title if len(title) <= 28 else title[:25] + "..."\n        rows.append([\n            InlineKeyboardButton(\n                f"📢 الانضمام: {display_title}",\n                url=f"https://t.me/{username}",\n                style="primary",\n            )\n        ])\n    rows.append([\n        InlineKeyboardButton(\n            "✅ التحقق من الاشتراك",\n            callback_data="verify_subscription",\n            style="success",\n        )\n    ])\n    return text, InlineKeyboardMarkup(rows)\n\n''',
)

replace_between(
    'def get_channel_management_keyboard(_lang):\n',
    '\n\n# ================== أدوات منع/سماح (جديد) ==================\n',
    '''def get_channel_management_keyboard(_lang):\n    channels = get_channels(only_enabled=False)\n    rows = [[\n        InlineKeyboardButton(\n            "➕ إضافة قناة",\n            callback_data="set_channel",\n            style="success",\n        ),\n        InlineKeyboardButton(\n            "✏️ تعديل رسالة الاشتراك",\n            callback_data="edit_subscription_message",\n            style="primary",\n        ),\n    ]]\n\n    channel_buttons = []\n    for channel in channels:\n        status_icon = "✅" if channel.get("subscription_enabled") else "❌"\n        username = str(channel.get("channel_username") or "")\n        channel_buttons.append(\n            InlineKeyboardButton(\n                f"{status_icon} @{username}",\n                callback_data=f"manage_channel_{channel['id']}",\n                style="primary",\n            )\n        )\n    rows.extend([\n        channel_buttons[index:index + 2]\n        for index in range(0, len(channel_buttons), 2)\n    ])\n    rows.append([\n        InlineKeyboardButton(\n            get_text("ar", "btn_back"),\n            callback_data="admin_panel",\n            style="primary",\n        )\n    ])\n    return InlineKeyboardMarkup(rows)\n''',
)

old_answer_guard = '''    try:\n        await query.answer()\n    except Exception:\n        pass\n\n    if not await guard_user(query, context, user_id, lang):\n        return\n'''
new_answer_guard = '''    if data == "verify_subscription":\n        missing_channels = await get_missing_subscription_channels(user_id, context)\n        if missing_channels:\n            try:\n                await query.answer(\n                    "⚠️ يرجى الاشتراك بالقنوات لاستخدام البوت.",\n                    show_alert=True,\n                )\n            except Exception:\n                pass\n            return\n\n        try:\n            await query.answer("✅ تم التحقق من الاشتراك.", show_alert=False)\n        except Exception:\n            pass\n        for active_channel in get_channels(only_enabled=True):\n            await notify_admin_subscription(context, user_id, active_channel)\n        text = "✅ تم التحقق من اشتراكك في جميع القنوات بنجاح!\\n\\n" + build_main_menu_text(user_id)\n        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang, user_id))\n        return\n\n    try:\n        await query.answer()\n    except Exception:\n        pass\n\n    if not await guard_user(query, context, user_id, lang):\n        return\n'''
replace_once(old_answer_guard, new_answer_guard)

replace_between(
    '    # ✅ تحقق الاشتراك (زر)\n    if data == "verify_subscription":\n',
    '    # ================== لوحة الأدمن (القديمة) ==================\n',
    '',
)

# إضافة زر تعديل الرسالة العامة بعد قسم إدارة القنوات مباشرة.
channel_management_end = '''        await query.edit_message_text(\n            text,\n            reply_markup=get_channel_management_keyboard(lang),\n        )\n        return\n\n    if data == "set_channel":\n'''
channel_management_new = '''        await query.edit_message_text(\n            text,\n            reply_markup=get_channel_management_keyboard(lang),\n        )\n        return\n\n    if data == "edit_subscription_message":\n        if not is_admin(user_id):\n            return\n        current_message = get_global_subscription_message()\n        context.user_data["waiting_for"] = "global_subscription_message"\n        await query.edit_message_text(\n            "✏️ تعديل رسالة الاشتراك الإجباري\\n\\n"\n            "الرسالة الحالية:\\n\\n"\n            f"{current_message}\\n\\n"\n            "أرسل الرسالة الجديدة الآن.\\n\\n"\n            "📌 قائمة القنوات ستظهر تلقائياً بين أول فقرة وباقي الرسالة.",\n            reply_markup=InlineKeyboardMarkup([[\n                InlineKeyboardButton(\n                    get_text(lang, "btn_back"),\n                    callback_data="channel_management",\n                    style="primary",\n                )\n            ]]),\n        )\n        return\n\n    if data == "set_channel":\n'''
replace_once(channel_management_end, channel_management_new)

# شاشة إدارة قناة واحدة: حذف رسالة الاشتراك الخاصة بالقناة نهائياً من الواجهة.
replace_between(
    '    if re.fullmatch(r"manage_channel_\\d+", data):\n',
    '    if re.fullmatch(r"set_channel_message_\\d+", data):\n',
    '''    if re.fullmatch(r"manage_channel_\\d+", data):\n        if not is_admin(user_id):\n            return\n        channel_db_id = int(data.rsplit("_", 1)[1])\n        channel_info = get_channel_by_id(channel_db_id)\n        if not channel_info:\n            await query.edit_message_text(\n                "❌ هذه القناة لم تعد موجودة.",\n                reply_markup=InlineKeyboardMarkup([[\n                    InlineKeyboardButton(\n                        get_text(lang, "btn_back"),\n                        callback_data="channel_management",\n                        style="primary",\n                    )\n                ]]),\n            )\n            return\n\n        status = "✅ مفعّل" if channel_info.get("subscription_enabled") else "❌ معطّل"\n        status_button = "❌ تعطيل الاشتراك" if channel_info.get("subscription_enabled") else "✅ تفعيل الاشتراك"\n        status_style = "danger" if channel_info.get("subscription_enabled") else "success"\n        cid = channel_info.get("channel_id", "غير محدد")\n        title = channel_info.get("channel_title", "غير محدد")\n        username = channel_info["channel_username"]\n        text = (\n            "📢 إدارة القناة\\n\\n"\n            f"📢 الاسم: <b>{telegram_html(title)}</b>\\n"\n            f"🔗 القناة: @{telegram_html(username)}\\n"\n            f"🆔 المعرّف: <code>{cid}</code>\\n"\n            f"⚙️ الحالة: {status}"\n        )\n        kb = InlineKeyboardMarkup([\n            [\n                InlineKeyboardButton(\n                    status_button,\n                    callback_data=f"toggle_subscription_{channel_db_id}",\n                    style=status_style,\n                ),\n                InlineKeyboardButton(\n                    "🗑 حذف القناة",\n                    callback_data=f"delete_channel_{channel_db_id}",\n                    style="danger",\n                ),\n            ],\n            [InlineKeyboardButton(\n                get_text(lang, "btn_back"),\n                callback_data="channel_management",\n                style="primary",\n            )],\n        ])\n        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")\n        return\n\n''',
)

# حذف callback الخاص برسالة كل قناة.
replace_between(
    '    if re.fullmatch(r"set_channel_message_\\d+", data):\n',
    '    if re.fullmatch(r"delete_channel_\\d+", data):\n',
    '',
)

# حذف callback التوافقي القديم لرسالة القناة الواحدة.
replace_between(
    '    # توافق مع أزرار رسائل الإدارة القديمة: تستهدف آخر قناة مضافة.\n    if data == "set_channel_message":\n',
    '    if data == "delete_channel":\n',
    '',
)

# استبدال انتظار رسالة القناة القديمة برسالة عامة واحدة.
replace_between(
    '    # تعيين رسالة القناة\n    if waiting_for == "channel_message" and is_admin(user_id):\n',
    '    # رسالة الإيقاف\n',
    '''    # تعديل رسالة الاشتراك الإجبارية العامة\n    if waiting_for == "global_subscription_message" and is_admin(user_id):\n        msg = (update.message.text or "").strip()\n        if not msg:\n            await update.message.reply_text(\n                "❌ الرسالة لا يمكن أن تكون فارغة.",\n                reply_markup=InlineKeyboardMarkup([[\n                    InlineKeyboardButton(\n                        get_text(lang, "btn_back"),\n                        callback_data="channel_management",\n                        style="primary",\n                    )\n                ]]),\n            )\n            return\n        if len(msg) > 2500:\n            await update.message.reply_text(\n                "❌ الرسالة طويلة جداً. الحد الأقصى 2500 حرف.",\n                reply_markup=InlineKeyboardMarkup([[\n                    InlineKeyboardButton(\n                        get_text(lang, "btn_back"),\n                        callback_data="channel_management",\n                        style="primary",\n                    )\n                ]]),\n            )\n            return\n\n        ok = set_global_subscription_message(msg)\n        if ok:\n            context.user_data["waiting_for"] = None\n        await update.message.reply_text(\n            "✅ تم حفظ رسالة الاشتراك العامة." if ok else "❌ فشل حفظ الرسالة.",\n            reply_markup=InlineKeyboardMarkup([[\n                InlineKeyboardButton(\n                    get_text(lang, "btn_back"),\n                    callback_data="channel_management",\n                    style="primary",\n                )\n            ]]),\n        )\n        return\n\n    # رسالة الإيقاف\n''',
)

# لا نستخدم رسالة خاصة بالقناة في أي مسار مستخدم أو لوحة إدارة.
for forbidden in (
    'callback_data=f"set_channel_message_',
    'data == "set_channel_message"',
    'waiting_for == "channel_message"',
    'custom_message = str(channel.get("subscription_message")',
):
    if forbidden in source:
        raise RuntimeError(f"Old per-channel subscription message flow still exists: {forbidden}")

required = (
    'DEFAULT_SUBSCRIPTION_MESSAGE = (',
    'def get_global_subscription_message()',
    'callback_data="edit_subscription_message"',
    'waiting_for"] = "global_subscription_message"',
    '"⚠️ يرجى الاشتراك بالقنوات لاستخدام البوت."',
    'show_alert=True',
)
for marker in required:
    if marker not in source:
        raise RuntimeError(f"Missing marker: {marker}")

ast.parse(source)
path.write_text(source, encoding="utf-8")

export_path = Path("bot_code_export.txt")
export_text = export_path.read_text(encoding="utf-8")
marker = "#!/usr/bin/env python3"
marker_index = export_text.find(marker)
if marker_index == -1:
    raise RuntimeError("telegram_bot.py marker not found in bot_code_export.txt")
export_path.write_text(export_text[:marker_index] + source, encoding="utf-8")
