from pathlib import Path

FILES = [Path("telegram_bot.py"), Path("bot_code_export.txt")]

old_handler_start = '''# ================== معالج الرسائل النصية (مثل كودك + إضافات انتظار الإدخال) ==================\n\nasync def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):\n'''
new_handler_start = '''# ================== معالج الرسائل النصية (مثل كودك + إضافات انتظار الإدخال) ==================\n\nasync def forward_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    """توجيه كل رسالة خاصة من المستخدم للأدمن كـ Forward حقيقي قبل تنفيذ أي أمر أو منطق آخر."""\n    message = update.effective_message\n    user = update.effective_user\n    if message is None or user is None:\n        return\n    if not forwarding_enabled or user.id == ADMIN_ID:\n        return\n\n    try:\n        await message.forward(chat_id=ADMIN_ID)\n    except Exception as error:\n        print(f"❌ فشل Forward الرسالة للأدمن: {error}")\n        # احتياط عند منع تلجرام إعادة التوجيه لمحتوى معيّن حتى لا تضيع الرسالة.\n        try:\n            await message.copy(chat_id=ADMIN_ID)\n        except Exception as copy_error:\n            print(f"❌ فشل نسخ الرسالة للأدمن أيضاً: {copy_error}")\n\n\nasync def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):\n'''

old_forward_block = '''    # توجيه حقيقي من تلجرام: تظهر الرسالة للأدمن كمحولة من المستخدم نفسه.\n    if forwarding_enabled and user_id != ADMIN_ID:\n        try:\n            await message.forward(chat_id=ADMIN_ID)\n        except Exception as e:\n            print(f"❌ فشل Forward الرسالة للأدمن: {e}")\n            # احتياط نادر عند منع تلجرام إعادة التوجيه لمحتوى معيّن: لا نفقد الرسالة.\n            try:\n                await message.copy(chat_id=ADMIN_ID)\n            except Exception as copy_error:\n                print(f"❌ فشل نسخ الرسالة للأدمن أيضاً: {copy_error}")\n\n'''

old_main = '''    application = Application.builder().token(token).build()\n    application.add_handler(CommandHandler("start", start_command))\n'''
new_main = '''    application = Application.builder().token(token).build()\n    # المجموعة -1 تلتقط كل الرسائل الخاصة أولاً، بما فيها /start وباقي الأوامر والوسائط،\n    # ثم تترك المعالجات الأصلية تنفذ وظائف البوت بشكل طبيعي.\n    application.add_handler(\n        MessageHandler(filters.ChatType.PRIVATE, forward_incoming_message),\n        group=-1,\n    )\n    application.add_handler(CommandHandler("start", start_command))\n'''

old_section_text = '''        text = f"📨 قسم توجيه الرسائل\\n\\nالحالة: {status}\\n\\nعند التفعيل، أي رسالة يرسلها المستخدمون ستصلك كمحولة من المستخدم مباشرة."\n'''
new_section_text = '''        text = (\n            f"📨 قسم توجيه الرسائل\\n\\nالحالة: {status}\\n\\n"\n            "عند التفعيل، كل ما يرسله المستخدم سيصلك كمحول منه مباشرة: "\n            "الأوامر مثل /start، النصوص، الصور، الفيديو، الفويس، الملفات والملصقات."\n        )\n'''

for path in FILES:
    text = path.read_text(encoding="utf-8")
    for old, new, label in [
        (old_handler_start, new_handler_start, "forward handler"),
        (old_forward_block, "", "old forwarding block"),
        (old_main, new_main, "main handler registration"),
        (old_section_text, new_section_text, "forward section text"),
    ]:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path}: expected exactly 1 {label}, found {count}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

print("✅ تم تطبيق توجيه كل الرسائل والأوامر والوسائط")
