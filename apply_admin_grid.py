from pathlib import Path
import ast

old = '''def get_admin_panel_keyboard(_lang, user_id):
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
'''

new = '''def get_admin_panel_keyboard(_lang, user_id):
    keyboard = [
        [
            InlineKeyboardButton("📊 قسم الإحصائيات", callback_data="section_stats", style="primary"),
            InlineKeyboardButton("📢 قسم الإذاعة", callback_data="section_broadcast", style="primary"),
        ],
        [
            InlineKeyboardButton("📨 قسم توجيه الرسائل", callback_data="section_forward", style="primary"),
            InlineKeyboardButton("📢 إدارة القنوات", callback_data="channel_management", style="primary"),
        ],
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="section_settings", style="primary"),
            InlineKeyboardButton("👥 إدارة الأعضاء", callback_data="section_members", style="primary"),
        ],
        [
            InlineKeyboardButton("🔢 حد إنشاء الإيميلات", callback_data="section_email_limit", style="primary"),
            InlineKeyboardButton("🌐 إدارة الدومينات المدفوعة", callback_data="section_paid_domains", style="primary"),
        ],
        [InlineKeyboardButton("🩺 حالة البوت والخدمات", callback_data="section_health", style="primary")],
    ]

    if user_id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton("👮 إدارة المشرفين", callback_data="section_admins", style="primary"),
            InlineKeyboardButton("🛑 الحظر / فك الحظر", callback_data="section_ban", style="danger"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🛑 الحظر / فك الحظر", callback_data="section_ban", style="danger")
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("👋 رسالة الترحيب", callback_data="section_welcome", style="success"),
            InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info", style="primary"),
        ],
        [InlineKeyboardButton(get_text("ar", "btn_back"), callback_data="back_to_menu", style="primary")],
    ])
    return InlineKeyboardMarkup(keyboard)
'''

for file_name in ("telegram_bot.py", "bot_code_export.txt"):
    path = Path(file_name)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{file_name}: expected one admin keyboard block, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

source = Path("telegram_bot.py").read_text(encoding="utf-8")
ast.parse(source)

start = source.index("def get_admin_panel_keyboard")
end = source.index("def paginate_member_items")
block = source[start:end]
for callback in (
    "section_stats",
    "section_broadcast",
    "section_forward",
    "channel_management",
    "section_settings",
    "section_members",
    "section_email_limit",
    "section_paid_domains",
    "section_health",
    "section_admins",
    "section_ban",
    "section_welcome",
    "bot_info",
    "back_to_menu",
):
    if callback not in block:
        raise RuntimeError(f"Missing callback: {callback}")
