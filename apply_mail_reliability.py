from pathlib import Path
import ast

path = Path("telegram_bot.py")
source = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match, found {count}: {old[:120]!r}")
    source = source.replace(old, new, 1)


def replace_between(start_marker: str, end_marker: str, replacement: str) -> None:
    global source
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start == -1 or end == -1:
        raise RuntimeError(f"Markers not found: {start_marker!r} -> {end_marker!r}")
    source = source[:start] + replacement + source[end:]


replace_once(
    "import asyncio\nimport os\nimport re\nimport time\nimport traceback\n",
    "import asyncio\nimport os\nimport re\nimport secrets\nimport string\nimport time\nimport traceback\n",
)

replace_once(
    "BOT_STARTED_AT = time.time()\n",
    "BOT_STARTED_AT = time.time()\n"
    "CREATE_EMAIL_COOLDOWN_SECONDS = 5\n"
    "INBOX_COOLDOWN_SECONDS = 3\n"
    "LEGACY_MAIL_PASSWORD = os.getenv(\"MAIL_TM_LEGACY_PASSWORD\", \"TempMail123\")\n"
    "USER_ACTION_TIMESTAMPS = {}\n",
)

replace_once(
    "\n\ndef _default_button_style(text: str, callback_data: str | None = None, url: str | None = None) -> str:\n",
    r'''

def consume_action_cooldown(user_id: int, action: str, cooldown_seconds: int) -> int:
    """يرجع الثواني المتبقية، أو صفر ويسجل العملية إذا كان مسموحاً تنفيذها."""
    now = time.monotonic()
    key = (int(user_id), str(action))
    last_time = USER_ACTION_TIMESTAMPS.get(key)
    if last_time is not None:
        remaining = cooldown_seconds - (now - last_time)
        if remaining > 0:
            return max(1, int(remaining) + 1)

    USER_ACTION_TIMESTAMPS[key] = now
    if len(USER_ACTION_TIMESTAMPS) > 5000:
        cutoff = now - 3600
        stale_keys = [item for item, stamp in USER_ACTION_TIMESTAMPS.items() if stamp < cutoff]
        for stale_key in stale_keys:
            USER_ACTION_TIMESTAMPS.pop(stale_key, None)
    return 0


def _default_button_style(text: str, callback_data: str | None = None, url: str | None = None) -> str:
''',
)

replace_between(
    "def get_available_domains():\n",
    "def create_email():\n",
    r'''def get_available_domains():
    response = mail_request("GET", "/domains")
    if response is None or response.status_code != 200:
        return []
    try:
        data = response.json()
    except ValueError as error:
        print(f"⚠️ رد النطاقات غير صالح: {error}")
        return []

    domains = data.get("hydra:member") or []
    available = []
    for item in domains:
        domain = item.get("domain")
        if not domain:
            continue
        if item.get("isActive") is False or item.get("isPrivate") is True:
            continue
        if domain not in available:
            available.append(domain)
    return available


''',
)

replace_between(
    "def create_email():\n",
    "def check_inbox_detailed(token):\n",
    r'''def create_email():
    """إنشاء بريد مع تجربة الدومينات المجانية المتاحة تلقائياً عند فشل أحدها."""
    try:
        domains = get_available_domains()
        if not domains:
            return None, None, None

        domains = list(domains)
        secrets.SystemRandom().shuffle(domains)
        username_chars = string.ascii_lowercase + string.digits
        password_chars = string.ascii_letters + string.digits

        for domain in domains:
            for _ in range(2):
                username = "".join(secrets.choice(username_chars) for _ in range(10))
                email_address = f"{username}@{domain}"
                password = "".join(secrets.choice(password_chars) for _ in range(20))

                response = mail_request(
                    "POST",
                    "/accounts",
                    json={"address": email_address, "password": password},
                )
                if response is None:
                    print(f"⚠️ تعذر إنشاء حساب على الدومين @{domain}، تجربة دومين آخر")
                    break

                if response.status_code == 422:
                    continue

                if response.status_code != 201:
                    print(
                        f"⚠️ فشل إنشاء حساب على @{domain}: HTTP {response.status_code}، "
                        "تجربة دومين آخر"
                    )
                    break

                token_response = mail_request(
                    "POST",
                    "/token",
                    json={"address": email_address, "password": password},
                )
                if token_response is None or token_response.status_code != 200:
                    status = token_response.status_code if token_response is not None else "network"
                    print(f"⚠️ فشل جلب توكن البريد {email_address}: {status}")
                    break

                try:
                    token = token_response.json().get("token")
                except (ValueError, AttributeError):
                    token = None
                if token:
                    return email_address, token, password
                break

        return None, None, None
    except Exception as error:
        print(f"❌ create_email: {type(error).__name__}: {error}")
        return None, None, None


def refresh_email_token_data(email_data):
    """تجديد توكن بريد واحد من العنوان وكلمة المرور المحفوظة."""
    if not isinstance(email_data, dict):
        return None

    address = str(email_data.get("address") or "").strip()
    password = str(email_data.get("password") or LEGACY_MAIL_PASSWORD)
    if not address or not password:
        return None

    response = mail_request(
        "POST",
        "/token",
        json={"address": address, "password": password},
    )
    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "network"
        print(f"⚠️ تعذر تجديد توكن {address}: {status}")
        return None

    try:
        token = response.json().get("token")
    except (ValueError, AttributeError):
        token = None
    if not token:
        return None

    email_data["token"] = token
    email_data["password"] = password
    return token


def refresh_user_email_token(user_id: int, email_index: int):
    """تجديد توكن بريد المستخدم وحفظه في PostgreSQL."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return None

    token = refresh_email_token_data(emails[email_index])
    if not token:
        return None

    data = get_user_data(user_id)
    user_database[str(user_id)] = data
    if not save_single_user(str(user_id), data):
        print(f"⚠️ تم تجديد التوكن لكن تعذر حفظه للمستخدم {user_id}")
    return token


def check_user_inbox_detailed(user_id: int, email_index: int):
    """فحص الصندوق وتجديد التوكن تلقائياً مرة واحدة عند HTTP 401."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return {"messages": None, "error": "email_missing", "status": None}

    email_data = emails[email_index]
    result = check_inbox_detailed(email_data.get("token"))
    if result.get("error") != "token_invalid":
        return result

    new_token = refresh_user_email_token(user_id, email_index)
    if not new_token:
        return result

    retry_result = check_inbox_detailed(new_token)
    retry_result["token_refreshed"] = retry_result.get("error") is None
    return retry_result


def get_user_message_content(user_id: int, email_index: int, message_id: str):
    """تحميل رسالة كاملة، مع تجديد التوكن تلقائياً إذا انتهى أثناء الفتح."""
    emails = get_user_emails(user_id)
    if email_index < 0 or email_index >= len(emails):
        return None

    token = emails[email_index].get("token")
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {token}"}
        response = mail_request("GET", f"/messages/{message_id}", headers=headers)
        if response is not None and response.status_code == 200:
            try:
                return response.json()
            except ValueError as error:
                print(f"⚠️ رد محتوى الرسالة غير صالح: {error}")
                return None

        if attempt == 0 and response is not None and response.status_code == 401:
            token = refresh_user_email_token(user_id, email_index)
            if token:
                continue
        return None
    return None


''',
)

replace_between(
    "def add_user_email(user_id, email, token):\n",
    "def remove_user_email(user_id, email):\n",
    r'''def add_user_email(user_id, email, token, password=None):
    data = get_user_data(user_id)
    email_record = {"address": email, "token": token}
    if password:
        email_record["password"] = password
    data.setdefault("emails", []).append(email_record)
    user_database[str(user_id)] = data
    save_single_user(str(user_id), data)


''',
)

replace_once(
    r'''    try:
        await query.answer()
    except Exception:
        pass

    user_id = user.id
    data = query.data or ""
    lang = "ar"

    if not await guard_user(query, context, user_id, lang):
''',
    r'''    user_id = user.id
    data = query.data or ""
    lang = "ar"

    cooldown_remaining = 0
    if data == "create_email":
        cooldown_remaining = consume_action_cooldown(
            user_id, "create_email", CREATE_EMAIL_COOLDOWN_SECONDS
        )
    elif re.fullmatch(r"inbox_\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "inbox", INBOX_COOLDOWN_SECONDS
        )

    if cooldown_remaining > 0:
        try:
            await query.answer(
                f"⏳ انتظر {cooldown_remaining} ثانية قبل إعادة المحاولة.",
                show_alert=False,
            )
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass

    if not await guard_user(query, context, user_id, lang):
''',
)

replace_once(
    "        email, token = await asyncio.to_thread(create_email)\n",
    "        email, token, password = await asyncio.to_thread(create_email)\n",
)
replace_once(
    "            add_user_email(user_id, email, token)\n",
    "            add_user_email(user_id, email, token, password)\n",
)

replace_once(
    '        inbox_result = await asyncio.to_thread(check_inbox_detailed, email_data["token"])\n',
    '        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, user_id, email_index)\n',
)

replace_once(
    r'''        messages = await asyncio.to_thread(check_inbox, email_data["token"])
        if not messages or msg_index >= len(messages):
            return
        msg_id = messages[msg_index]["id"]

        full = await asyncio.to_thread(get_message_content, msg_id, email_data["token"])
        if not full:
''',
    r'''        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, user_id, email_index)
        messages = inbox_result.get("messages")
        if inbox_result.get("error") is not None:
            error_text, error_keyboard = build_inbox_error_view(
                inbox_result.get("error"),
                email_index,
                inbox_result.get("status"),
            )
            await query.edit_message_text(error_text, reply_markup=error_keyboard)
            return
        if not messages or msg_index >= len(messages):
            return
        msg_id = messages[msg_index]["id"]

        full = await asyncio.to_thread(get_user_message_content, user_id, email_index, msg_id)
        if not full:
''',
)

replace_once(
    '        text = f"📧 <code>{email_data[\'address\']}</code>\\n🔑 <code>TempMail123</code>"\n',
    '        email_password = email_data.get("password") or LEGACY_MAIL_PASSWORD\n'
    '        text = f"📧 <code>{email_data[\'address\']}</code>\\n🔑 <code>{telegram_html(email_password)}</code>"\n',
)

replace_once(
    r'''        text = (
            "⚠️ توكن هذا البريد غير صالح أو منتهي.\n\n"
            "لم يعد بالإمكان تحميل رسائله. يرجى حذف هذا البريد وإنشاء بريد جديد."
        )
''',
    r'''        text = (
            "⚠️ تعذر تجديد جلسة هذا البريد تلقائياً.\n\n"
            "قد تكون بيانات البريد قديمة أو لم يعد الحساب متاحاً على Mail.tm."
        )
''',
)

ast.parse(source)
for marker in (
    "consume_action_cooldown",
    "CREATE_EMAIL_COOLDOWN_SECONDS = 5",
    "INBOX_COOLDOWN_SECONDS = 3",
    "refresh_user_email_token",
    "check_user_inbox_detailed",
    "get_user_message_content",
    "secrets.SystemRandom().shuffle(domains)",
    "add_user_email(user_id, email, token, password)",
):
    if marker not in source:
        raise RuntimeError(f"Missing required marker: {marker}")

if "application.job_queue" in source or "run_repeating" in source:
    raise RuntimeError("Unexpected background inbox polling was introduced")

path.write_text(source, encoding="utf-8")

export_path = Path("bot_code_export.txt")
export_text = export_path.read_text(encoding="utf-8")
marker = "#!/usr/bin/env python3"
marker_index = export_text.find(marker)
if marker_index == -1:
    raise RuntimeError("telegram_bot.py marker not found in bot_code_export.txt")
export_prefix = export_text[:marker_index]
export_path.write_text(export_prefix + source, encoding="utf-8")
