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

# نتأكد أن رسالة الخطأ الخاصة بالمستخدم فقط لم تعد تعرض اسم الخدمة.
user_marker = 'def build_inbox_error_view('
user_pos = source.find(user_marker)
if user_pos < 0:
    raise SystemExit('normal-user inbox error function was not found')
next_def = source.find('\ndef ', user_pos + len(user_marker))
if next_def < 0:
    next_def = len(source)
user_block = source[user_pos:next_def]
if 'Mail.tm' in user_block or 'mail.tm' in user_block:
    raise SystemExit('service name still appears in the normal-user inbox error function')
if OLD not in source:
    raise SystemExit('admin-only error wording was unexpectedly changed')
