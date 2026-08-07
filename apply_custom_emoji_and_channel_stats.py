from pathlib import Path
import ast

SOURCE_PATH = Path("telegram_bot.py")
EXPORT_PATH = Path("bot_code_export.txt")
source = SOURCE_PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 occurrence, found {count}")
    source = source.replace(old, new, 1)


replace_once(
    "import asyncio\nimport os\n",
    "import asyncio\nimport json\nimport os\n",
    "json import",
)

replace_once(
    'bot_offline_message = ""\n',
    'bot_offline_message = ""\nbot_offline_message_html = ""\n',
    "offline rich global",
)

rich_helpers = r'''

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
'''

marker = "\ndef increment_daily_stat(stat_name: str) -> bool:\n"
if marker not in source:
    raise RuntimeError("rich helper insertion marker not found")
source = source.replace(marker, rich_helpers + marker, 1)

old_global_subscription = r'''def get_global_subscription_message() -> str:
    """رسالة اشتراك إجبارية عامة واحدة لكل القنوات."""
    value = get_setting("global_subscription_message", DEFAULT_SUBSCRIPTION_MESSAGE).strip()
    return value or DEFAULT_SUBSCRIPTION_MESSAGE


def set_global_subscription_message(message: str) -> bool:
    value = str(message or "").strip()
    if not value:
        return False
    return set_setting("global_subscription_message", value)
'''
new_global_subscription = r'''def get_global_subscription_message() -> str:
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
'''
replace_once(old_global_subscription, new_global_subscription, "global subscription helpers")

old_main_menu = r'''def build_main_menu_text(user_id: int) -> str:
    """دمج رسالة الترحيب مع القائمة الرئيسية في رسالة واحدة."""
    emails_count = len(get_user_emails(user_id))
    menu_text = get_text("ar", "main_menu", emails_count=emails_count)
    welcome_message = get_setting("welcome_message", "").strip()
    if welcome_message:
        return f"{welcome_message}\n\n{menu_text}"
    return menu_text
'''
new_main_menu = r'''def build_main_menu_text(user_id: int) -> str:
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
'''
replace_once(old_main_menu, new_main_menu, "main menu rich")

stats_helper = r'''

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
'''
marker = "\n\n# ================== اشتراك إجباري متعدد ==================\n"
if marker not in source:
    raise RuntimeError("channel stats insertion marker not found")
source = source.replace(marker, stats_helper + marker, 1)

replace_once(
    '        channel_lines.append(f"{index}. 📢 {title} — @{username}")\n',
    '        channel_lines.append(\n            f"{index}. 📢 {telegram_html(title)} — @{telegram_html(username)}"\n        )\n',
    "subscription channel html",
)
replace_once(
    "    global_message = get_global_subscription_message()\n",
    "    global_message = get_global_subscription_message_html()\n",
    "subscription rich message",
)

old_channel_keyboard_start = r'''    rows = [[
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

    channel_buttons = []
'''
new_channel_keyboard_start = r'''    rows = [[
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
'''
replace_once(old_channel_keyboard_start, new_channel_keyboard_start, "channel stats button")

old_offline_guard = r'''    # بوت مطفي؟
    if not bot_active and not admin_user:
        text = f"⚠️ البوت متوقف مؤقتاً\n\n{bot_offline_message}" if bot_offline_message else "⚠️ البوت متوقف مؤقتاً."
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(text)
        else:
            try:
                await update_or_query.edit_message_text(text)
            except:
                pass
        return False
'''
new_offline_guard = r'''    # بوت مطفي؟
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
'''
replace_once(old_offline_guard, new_offline_guard, "offline rich display")

old_subscription_guard = r'''            text, kb = subscription_prompt(lang, missing_channels)
            if hasattr(update_or_query, "message") and update_or_query.message:
                await update_or_query.message.reply_text(text, reply_markup=kb)
            else:
                try:
                    await update_or_query.edit_message_text(text, reply_markup=kb)
                except Exception:
                    pass
'''
new_subscription_guard = r'''            text, kb = subscription_prompt(lang, missing_channels)
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
'''
replace_once(old_subscription_guard, new_subscription_guard, "subscription prompt HTML")

old_start_menu = r'''    await message.reply_text(
        build_main_menu_text(user_id),
        reply_markup=get_main_menu_keyboard(lang, user_id),
    )
'''
new_start_menu = r'''    await message.reply_text(
        build_main_menu_html(user_id),
        reply_markup=get_main_menu_keyboard(lang, user_id),
        parse_mode="HTML",
    )
'''
replace_once(old_start_menu, new_start_menu, "start menu rich")

old_verify_success = r'''        text = "✅ تم التحقق من اشتراكك في جميع القنوات بنجاح!\n\n" + build_main_menu_text(user_id)
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang, user_id))
'''
new_verify_success = r'''        text = (
            "✅ تم التحقق من اشتراكك في جميع القنوات بنجاح!\n\n"
            + build_main_menu_html(user_id)
        )
        await query.edit_message_text(
            text,
            reply_markup=get_main_menu_keyboard(lang, user_id),
            parse_mode="HTML",
        )
'''
replace_once(old_verify_success, new_verify_success, "verify rich main menu")

old_back_menu = r'''        await query.edit_message_text(
            build_main_menu_text(user_id),
            reply_markup=get_main_menu_keyboard(lang, user_id),
        )
'''
new_back_menu = r'''        await query.edit_message_text(
            build_main_menu_html(user_id),
            reply_markup=get_main_menu_keyboard(lang, user_id),
            parse_mode="HTML",
        )
'''
replace_once(old_back_menu, new_back_menu, "back menu rich")

channel_stats_callback = r'''
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

'''
marker = '    if data == "edit_subscription_message":\n'
if marker not in source:
    raise RuntimeError("channel stats callback marker not found")
source = source.replace(marker, channel_stats_callback + marker, 1)

old_edit_sub = r'''        current_message = get_global_subscription_message()
        context.user_data["waiting_for"] = "global_subscription_message"
        await query.edit_message_text(
            "✏️ تعديل رسالة الاشتراك الإجباري\n\n"
            "الرسالة الحالية:\n\n"
            f"{current_message}\n\n"
            "أرسل الرسالة الجديدة الآن.\n\n"
            "📌 قائمة القنوات ستظهر تلقائياً بين أول فقرة وباقي الرسالة.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
        )
'''
new_edit_sub = r'''        current_message = get_global_subscription_message_html()
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
'''
replace_once(old_edit_sub, new_edit_sub, "edit subscription rich preview")

old_welcome_section = r'''        current = get_setting("welcome_message", "")
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("✏️ تعيين رسالة الترحيب", callback_data="set_welcome_message", style="success"),
            InlineKeyboardButton("🧹 حذف رسالة الترحيب", callback_data="clear_welcome_message", style="danger"),
        ], "admin_panel")
        text = "👋 رسالة الترحيب الحالية:\n\n"
        text += (current if current else "— لا توجد رسالة —")
        await query.edit_message_text(text, reply_markup=kb)
'''
new_welcome_section = r'''        current, current_html = get_rich_text_setting("welcome_message", "")
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("✏️ تعيين رسالة الترحيب", callback_data="set_welcome_message", style="success"),
            InlineKeyboardButton("🧹 حذف رسالة الترحيب", callback_data="clear_welcome_message", style="danger"),
        ], "admin_panel")
        text = "👋 رسالة الترحيب الحالية:\n\n"
        text += (current_html if str(current or "").strip() else "— لا توجد رسالة —")
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
'''
replace_once(old_welcome_section, new_welcome_section, "welcome rich preview")

replace_once(
    '        set_setting("welcome_message", "")\n',
    '        set_setting("welcome_message", "")\n        set_setting("welcome_message_rich_html", "")\n',
    "clear welcome rich",
)

replace_once(
    'async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    global forwarding_enabled, bot_offline_message\n',
    'async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    global forwarding_enabled, bot_offline_message, bot_offline_message_html\n',
    "message handler globals",
)

replace_once(
    '                f"💬 الرسالة:\\n{telegram_html(message.text)}"\n',
    '                f"💬 الرسالة:\\n{message_custom_emoji_html(message)}"\n',
    "forward custom emoji",
)

old_global_handler_save = r'''        msg = (update.message.text or "").strip()
        if not msg:
'''
new_global_handler_save = r'''        msg = update.message.text or ""
        if not msg.strip():
'''
replace_once(old_global_handler_save, new_global_handler_save, "subscription preserve offsets")
replace_once(
    "        ok = set_global_subscription_message(msg)\n",
    '        ok = save_rich_text_setting("global_subscription_message", message)\n',
    "subscription rich save",
)

old_offline_handler = r'''    if waiting_for == "offline_message" and is_admin(user_id):
        bot_offline_message = (update.message.text or "").strip()
        context.user_data["waiting_for"] = None
        await update.message.reply_text("✅ تم حفظ رسالة الإيقاف",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]))
        return
'''
new_offline_handler = r'''    if waiting_for == "offline_message" and is_admin(user_id):
        bot_offline_message = update.message.text or ""
        bot_offline_message_html = message_custom_emoji_html(message)
        context.user_data["waiting_for"] = None
        await update.message.reply_text("✅ تم حفظ رسالة الإيقاف",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_settings")]]))
        return
'''
replace_once(old_offline_handler, new_offline_handler, "offline rich save")

old_broadcast_all = r'''        context.user_data["waiting_for"] = None
        msg = update.message.text or ""
        wait_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة...")
        okc = 0
        fail = 0
        for uid in list(user_database.keys()):
            try:
                await context.bot.send_message(chat_id=int(uid), text=f"📢 رسالة من الإدارة:\n\n{msg}")
                okc += 1
'''
new_broadcast_all = r'''        context.user_data["waiting_for"] = None
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
'''
replace_once(old_broadcast_all, new_broadcast_all, "broadcast all rich")

old_broadcast_active = r'''        context.user_data["waiting_for"] = None
        msg = update.message.text or ""
        wait_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة للنشطين...")
        okc = 0
        fail = 0
        for uid, info in user_database.items():
            if len(info.get("emails", [])) > 0:
                try:
                    await context.bot.send_message(chat_id=int(uid), text=f"📢 رسالة من الإدارة:\n\n{msg}")
                    okc += 1
'''
new_broadcast_active = r'''        context.user_data["waiting_for"] = None
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
'''
replace_once(old_broadcast_active, new_broadcast_active, "broadcast active rich")

old_welcome_handler = r'''    if waiting_for == "welcome_message" and is_admin(user_id):
        msg = (update.message.text or "").strip()
        set_setting("welcome_message", msg)
        context.user_data["waiting_for"] = None
        await update.message.reply_text("✅ تم حفظ رسالة الترحيب",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]))
        return
'''
new_welcome_handler = r'''    if waiting_for == "welcome_message" and is_admin(user_id):
        msg = update.message.text or ""
        ok = save_rich_text_setting("welcome_message", message)
        if ok:
            context.user_data["waiting_for"] = None
        await update.message.reply_text(
            "✅ تم حفظ رسالة الترحيب والإيموجيات المميزة" if ok else "❌ فشل حفظ رسالة الترحيب",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_welcome")]]),
        )
        return
'''
replace_once(old_welcome_handler, new_welcome_handler, "welcome rich save")

# تأكد أن النصوص الغنية الأساسية أصبحت مستخدمة فعلاً.
for required in (
    "message_custom_emoji_html(message)",
    "save_rich_text_setting(\"welcome_message\", message)",
    "save_rich_text_setting(\"global_subscription_message\", message)",
    'callback_data="channel_stats"',
    'if data == "channel_stats":',
):
    if required not in source:
        raise RuntimeError(f"missing required marker: {required}")

ast.parse(source)
SOURCE_PATH.write_text(source, encoding="utf-8")

export = EXPORT_PATH.read_text(encoding="utf-8")
export_marker = (
    "================================================================================\n"
    "📁 telegram_bot.py\n"
    "================================================================================\n"
)
marker_index = export.find(export_marker)
if marker_index == -1:
    raise RuntimeError("telegram_bot.py export marker not found")
content_start = marker_index + len(export_marker)
EXPORT_PATH.write_text(export[:content_start] + source, encoding="utf-8")
print("OK: custom emoji support and channel stats applied")
