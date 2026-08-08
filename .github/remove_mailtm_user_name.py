from pathlib import Path

OLD = 'قد تكون بيانات البريد قديمة أو لم يعد الحساب متاحاً على Mail.tm.'
NEW = 'قد تكون بيانات البريد قديمة أو لم يعد البريد متاحاً حالياً.'

for filename in ('telegram_bot.py', 'bot_code_export.txt'):
    path = Path(filename)
    text = path.read_text(encoding='utf-8')
    count = text.count(OLD)
    if count < 2:
        raise SystemExit(f'{filename}: expected at least 2 occurrences before patch, found {count}')
    text = text.replace(OLD, NEW, 1)
    if text.count(NEW) != 1:
        raise SystemExit(f'{filename}: user-facing replacement count is not exactly 1')
    if text.count(OLD) != count - 1:
        raise SystemExit(f'{filename}: unexpected remaining occurrence count')
    path.write_text(text, encoding='utf-8')

source = Path('telegram_bot.py').read_text(encoding='utf-8')
compile(source, 'telegram_bot.py', 'exec')

# نتأكد أن النسخة الخاصة بالمستخدم لم تعد تعرض اسم الخدمة، مع إبقاء النسخة الخاصة بالأدمن دون تغيير.
user_marker = 'def build_inbox_error_view('
admin_marker = 'def build_admin_member_inbox_error_view('
user_pos = source.find(user_marker)
admin_pos = source.find(admin_marker)
if user_pos < 0 or admin_pos < 0:
    raise SystemExit('required inbox error functions were not found')
user_block = source[user_pos:admin_pos]
if 'Mail.tm' in user_block or 'mail.tm' in user_block:
    raise SystemExit('service name still appears in the normal-user inbox error block')
if OLD not in source[admin_pos:]:
    raise SystemExit('admin-only error wording was unexpectedly changed')
