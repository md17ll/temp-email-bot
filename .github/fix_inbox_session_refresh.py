from pathlib import Path

FILES = ("telegram_bot.py", "bot_code_export.txt")

IMPORT_OLD = "import time\nimport traceback\nfrom datetime import timedelta\n"
IMPORT_NEW = "import threading\nimport time\nimport traceback\nfrom collections import deque\nfrom datetime import timedelta\n"

CONSTANTS_OLD = 'FIXED_MAIL_PASSWORD = "TempMail123"\nUSER_ACTION_TIMESTAMPS = {}\n'
CONSTANTS_NEW = (
    'FIXED_MAIL_PASSWORD = "TempMail123"\n'
    'USER_ACTION_TIMESTAMPS = {}\n'
    'MAIL_REQUEST_MAX_PER_SECOND = 6\n'
    'MAIL_REQUEST_WINDOW_SECONDS = 1.0\n'
    'MAIL_REQUEST_TIMESTAMPS = deque()\n'
    'MAIL_REQUEST_LOCK = threading.Lock()\n'
)

FUNCTION_MARKER = ")\n\n\ndef consume_action_cooldown(user_id: int, action: str, cooldown_seconds: int) -> int:\n"
RATE_LIMIT_FUNCTION = ''' )


def wait_for_mail_request_slot():
    """تنظيم طلبات خدمة البريد حتى لا تخرج دفعات كبيرة متزامنة من نفس السيرفر."""
    while True:
        with MAIL_REQUEST_LOCK:
            now = time.monotonic()
            while (
                MAIL_REQUEST_TIMESTAMPS
                and now - MAIL_REQUEST_TIMESTAMPS[0] >= MAIL_REQUEST_WINDOW_SECONDS
            ):
                MAIL_REQUEST_TIMESTAMPS.popleft()

            if len(MAIL_REQUEST_TIMESTAMPS) < MAIL_REQUEST_MAX_PER_SECOND:
                MAIL_REQUEST_TIMESTAMPS.append(now)
                return

            wait_seconds = MAIL_REQUEST_WINDOW_SECONDS - (
                now - MAIL_REQUEST_TIMESTAMPS[0]
            )

        time.sleep(max(0.01, wait_seconds + 0.01))


def consume_action_cooldown(user_id: int, action: str, cooldown_seconds: int) -> int:
'''.replace("''' )", ")")

MAIL_REQUEST_OLD = '''    for attempt in range(3):
        try:
            response = requests.request(
'''
MAIL_REQUEST_NEW = '''    for attempt in range(3):
        try:
            wait_for_mail_request_slot()
            response = requests.request(
'''

REFRESH_BLOCK = '''def refresh_email_token_data_detailed(email_data):
    """تجديد جلسة بريد واحد مع إرجاع سبب الفشل بدقة."""
    if not isinstance(email_data, dict):
        return {"token": None, "error": "token_invalid", "status": None}

    address = str(email_data.get("address") or "").strip()
    password = str(email_data.get("password") or LEGACY_MAIL_PASSWORD)
    if not address or not password:
        return {"token": None, "error": "token_invalid", "status": None}

    response, request_error = mail_request(
        "POST",
        "/token",
        json={"address": address, "password": password},
        return_error=True,
    )

    if response is None:
        error_code = request_error or "network"
        print(f"⚠️ تعذر تجديد جلسة {address}: {error_code}")
        return {"token": None, "error": error_code, "status": None}

    status = response.status_code
    if status == 200:
        try:
            token = response.json().get("token")
        except (ValueError, AttributeError):
            token = None

        if not token:
            print(f"⚠️ رد تجديد الجلسة غير صالح للبريد {address}")
            return {"token": None, "error": "invalid_response", "status": status}

        email_data["token"] = token
        email_data["password"] = password
        return {"token": token, "error": None, "status": status}

    if status == 429:
        error_code = "rate_limited"
    elif status in (500, 502, 503, 504):
        error_code = "service_unavailable"
    elif status in (400, 401, 403, 404, 422):
        error_code = "token_invalid"
    else:
        error_code = "http_error"

    print(f"⚠️ فشل تجديد جلسة {address}: HTTP {status} ({error_code})")
    return {"token": None, "error": error_code, "status": status}


def refresh_email_token_data(email_data):
    """واجهة متوافقة ترجع التوكن فقط للأجزاء القديمة."""
    return refresh_email_token_data_detailed(email_data).get("token")


def refresh_user_email_token_detailed(user_id: int, email_index: int):
    """تجديد جلسة بريد المستخدم وحفظها مع إرجاع سبب الفشل."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return {"token": None, "error": "email_missing", "status": None}

    result = refresh_email_token_data_detailed(emails[email_index])
    token = result.get("token")
    if not token:
        return result

    data = get_user_data(user_id)
    user_database[str(user_id)] = data
    if not save_single_user(str(user_id), data):
        print(f"⚠️ تم تجديد التوكن لكن تعذر حفظه للمستخدم {user_id}")
    return result


def refresh_user_email_token(user_id: int, email_index: int):
    """واجهة متوافقة ترجع التوكن فقط للأجزاء القديمة."""
    return refresh_user_email_token_detailed(user_id, email_index).get("token")


def check_user_inbox_detailed(user_id: int, email_index: int):
    """فحص الصندوق وتجديد الجلسة مرة واحدة مع الحفاظ على سبب الفشل الحقيقي."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return {"messages": None, "error": "email_missing", "status": None}

    email_data = emails[email_index]
    result = check_inbox_detailed(email_data.get("token"))
    if result.get("error") != "token_invalid":
        return result

    refresh_result = refresh_user_email_token_detailed(user_id, email_index)
    new_token = refresh_result.get("token")
    if not new_token:
        refresh_error = refresh_result.get("error")
        if refresh_error in (
            "rate_limited",
            "service_unavailable",
            "timeout",
            "connection",
            "network",
            "invalid_response",
            "http_error",
        ):
            return {
                "messages": None,
                "error": refresh_error,
                "status": refresh_result.get("status"),
            }
        return result

    retry_result = check_inbox_detailed(new_token)
    retry_result["token_refreshed"] = retry_result.get("error") is None
    return retry_result

'''

for filename in FILES:
    path = Path(filename)
    text = path.read_text(encoding="utf-8")

    if text.count(IMPORT_OLD) != 1:
        raise SystemExit(f"{filename}: import marker count changed")
    text = text.replace(IMPORT_OLD, IMPORT_NEW, 1)

    if text.count(CONSTANTS_OLD) != 1:
        raise SystemExit(f"{filename}: constants marker count changed")
    text = text.replace(CONSTANTS_OLD, CONSTANTS_NEW, 1)

    if text.count(FUNCTION_MARKER) != 1:
        raise SystemExit(f"{filename}: cooldown marker count changed")
    text = text.replace(FUNCTION_MARKER, RATE_LIMIT_FUNCTION, 1)

    if text.count(MAIL_REQUEST_OLD) != 1:
        raise SystemExit(f"{filename}: mail_request marker count changed")
    text = text.replace(MAIL_REQUEST_OLD, MAIL_REQUEST_NEW, 1)

    start_marker = "\ndef refresh_email_token_data(email_data):\n"
    end_marker = "\ndef get_user_message_content(user_id: int, email_index: int, message_id: str):\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start + 1)
    if start < 0 or end < 0 or end <= start:
        raise SystemExit(f"{filename}: refresh block markers were not found")
    text = text[:start + 1] + REFRESH_BLOCK + text[end + 1:]

    path.write_text(text, encoding="utf-8")

source = Path("telegram_bot.py").read_text(encoding="utf-8")
compile(source, "telegram_bot.py", "exec")

required = (
    "MAIL_REQUEST_MAX_PER_SECOND = 6",
    "def wait_for_mail_request_slot():",
    "wait_for_mail_request_slot()",
    "def refresh_email_token_data_detailed(email_data):",
    'error_code = "rate_limited"',
    'error_code = "service_unavailable"',
    "refresh_user_email_token_detailed(user_id, email_index)",
)
for item in required:
    if item not in source:
        raise SystemExit(f"missing expected fix marker: {item}")

user_start = source.find("def build_inbox_error_view(")
admin_start = source.find("def build_admin_member_inbox_error_view(")
if user_start < 0 or admin_start < 0 or admin_start <= user_start:
    raise SystemExit("inbox error view markers were not found")
user_error_block = source[user_start:admin_start]
if "Mail.tm" in user_error_block or "mail.tm" in user_error_block:
    raise SystemExit("service name leaked into the normal-user inbox error view")

# لا نغيّر التدفق اليدوي للبريد ولا طريقة إنشاء الإيميلات أو كلمة السر أو القوائم.
for untouched_marker in (
    'FIXED_MAIL_PASSWORD = "TempMail123"',
    'callback_data="create_email_fast"',
    'callback_data="select_free_domain"',
    'callback_data="change_domain"',
    'callback_data="select_inbox"',
):
    if untouched_marker not in source:
        raise SystemExit(f"unrelated behavior marker disappeared: {untouched_marker}")
