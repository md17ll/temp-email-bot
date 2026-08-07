from pathlib import Path

FILES = [Path("telegram_bot.py"), Path("bot_code_export.txt")]

replacements = [
    (
'''        text = f"📨 قسم توجيه الرسائل\\n\\nالحالة: {status}\\n\\nعند التفعيل، أي رسالة يرسلها المستخدمون ستصلك مباشرة."''',
'''        text = f"📨 قسم توجيه الرسائل\\n\\nالحالة: {status}\\n\\nعند التفعيل، أي رسالة يرسلها المستخدمون ستصلك كمحولة من المستخدم مباشرة."'''
    ),
    (
'''    # توجيه رسائل المستخدمين للأدمن إذا مفعّل
    if forwarding_enabled and user_id != ADMIN_ID:
        try:
            user_name = user.first_name or ""
            if user.last_name:
                user_name += f" {user.last_name}"
            username = f"@{user.username}" if user.username else "لا يوجد"
            forward_text = (
                "📨 <b>رسالة جديدة من مستخدم:</b>\\n\\n"
                f"👤 الاسم: {telegram_html(user_name)}\\n"
                f"🆔 المعرف: {telegram_html(username)}\\n"
                f"🔢 ID: <code>{user_id}</code>\\n"
                f"━━━━━━━━━━━━━━━\\n"
                f"💬 الرسالة:\\n{message_custom_emoji_html(message)}"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode="HTML")
        except Exception as e:
            print(f"❌ فشل توجيه الرسالة للأدمن: {e}")
''',
'''    # توجيه حقيقي من تلجرام: تظهر الرسالة للأدمن كمحولة من المستخدم نفسه.
    if forwarding_enabled and user_id != ADMIN_ID:
        try:
            await message.forward(chat_id=ADMIN_ID)
        except Exception as e:
            print(f"❌ فشل Forward الرسالة للأدمن: {e}")
            # احتياط نادر عند منع تلجرام إعادة التوجيه لمحتوى معيّن: لا نفقد الرسالة.
            try:
                await message.copy(chat_id=ADMIN_ID)
            except Exception as copy_error:
                print(f"❌ فشل نسخ الرسالة للأدمن أيضاً: {copy_error}")
'''
    ),
    (
'''    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        message_handler,
    ))''',
'''    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE,
        message_handler,
    ))'''
    ),
]

for path in FILES:
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path}: expected exactly one match, found {count}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

print("forwarding patch applied")
