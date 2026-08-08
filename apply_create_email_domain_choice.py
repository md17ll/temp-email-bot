from pathlib import Path

path = Path("telegram_bot.py")
text = path.read_text(encoding="utf-8")

# 1) إضافة إنشاء إيميل على دومين مجاني محدد، مع التحقق أنه ما زال متاحاً ونشطاً وغير خاص.
if "def create_email_with_domain(" not in text:
    anchor = "\n\ndef refresh_email_token_data(email_data):\n"
    if anchor not in text:
        raise SystemExit("refresh_email_token_data anchor not found")
    helper = '''

def create_email_with_domain(domain):
    """إنشاء بريد على دومين مجاني محدد من قائمة mail.tm المتاحة حالياً."""
    try:
        domain = str(domain or "").strip().lower().lstrip("@")
        if not domain:
            return None, None, None

        available_domains = get_available_domains()
        if domain not in available_domains:
            print(f"⚠️ الدومين @{domain} لم يعد متاحاً ضمن الدومينات المجانية العامة")
            return None, None, None

        username_chars = string.ascii_lowercase + string.digits
        password_chars = string.ascii_letters + string.digits

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
                break
            if response.status_code == 422:
                continue
            if response.status_code != 201:
                print(f"⚠️ فشل إنشاء حساب على @{domain}: HTTP {response.status_code}")
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
        print(f"❌ create_email_with_domain: {type(error).__name__}: {error}")
        return None, None, None
'''
    text = text.replace(anchor, helper + anchor, 1)

# 2) لوحة دومينات مجانية ملونة.
if "def get_free_domains_keyboard(" not in text:
    anchor = "\n\ndef get_email_list_keyboard(emails, action_prefix, _lang):\n"
    if anchor not in text:
        raise SystemExit("get_email_list_keyboard anchor not found")
    keyboard_helper = '''

def get_free_domains_keyboard(domains):
    """عرض الدومينات المجانية النشطة التي تم جلبها مباشرة من mail.tm."""
    rows = []
    for index, domain in enumerate(domains):
        display_domain = str(domain)
        if len(display_domain) > 40:
            display_domain = display_domain[:37] + "..."
        rows.append([
            InlineKeyboardButton(
                f"@{display_domain}",
                callback_data=f"free_domain_{index}",
                style="primary",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🔄 تحديث الدومينات",
            callback_data="refresh_free_domains",
            style="success",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data="create_email",
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(rows)
'''
    text = text.replace(anchor, keyboard_helper + anchor, 1)

# 3) مهلة الإنشاء تكون على عملية الإنشاء الفعلية، وليس مجرد فتح قائمة الطرق.
old_cooldown = '''    cooldown_remaining = 0
    if data == "create_email":
        cooldown_remaining = consume_action_cooldown(
            user_id, "create_email", CREATE_EMAIL_COOLDOWN_SECONDS
        )
    elif re.fullmatch(r"inbox_\\d+", data):
'''
new_cooldown = '''    cooldown_remaining = 0
    if data == "create_email_fast" or re.fullmatch(r"free_domain_\\d+", data):
        cooldown_remaining = consume_action_cooldown(
            user_id, "create_email", CREATE_EMAIL_COOLDOWN_SECONDS
        )
    elif data == "refresh_free_domains":
        cooldown_remaining = consume_action_cooldown(
            user_id, "free_domains", INBOX_COOLDOWN_SECONDS
        )
    elif re.fullmatch(r"inbox_\\d+", data):
'''
if old_cooldown not in text:
    raise SystemExit("create-email cooldown anchor not found")
text = text.replace(old_cooldown, new_cooldown, 1)

# 4) استبدال الإنشاء المباشر بقائمة: إنشاء سريع أو اختيار الدومين.
start = text.find('    # إنشاء إيميل\n    if data == "create_email":\n')
end = text.find('    # إيميلاتي\n', start)
if start == -1 or end == -1:
    raise SystemExit("create-email callback block not found")

new_block = '''    # إنشاء إيميل: يختار المستخدم بين الإنشاء السريع أو دومين مجاني محدد.
    if data == "create_email":
        current_count = len(get_user_emails(user_id))
        email_limit = get_effective_email_limit(user_id)
        if (not is_admin(user_id)) and email_limit > 0 and current_count >= email_limit:
            contact_username = get_admin_contact_username()
            rows = []
            if contact_username:
                rows.append([
                    InlineKeyboardButton(
                        "💬 التواصل مع الأدمن",
                        url=f"https://t.me/{contact_username}",
                        style="primary",
                    )
                ])
            rows.append([
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="back_to_menu",
                    style="primary",
                )
            ])
            await query.edit_message_text(
                "⚠️ لقد وصلت إلى الحد المسموح لإنشاء الإيميلات.\n\n"
                "يرجى التواصل مع الأدمن لإنشاء المزيد من الإيميلات.",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        text = (
            "✨ إنشاء إيميل جديد\n\n"
            "من هنا يمكنك اختيار طريقة إنشاء بريدك الإلكتروني.\n\n"
            "🎲 الإنشاء السريع:\n"
            "ينشئ لك البوت إيميل جديد مباشرة ويختار أحد\n"
            "الدومينات المجانية المتاحة تلقائياً.\n\n"
            "🌐 اختيار الدومين:\n"
            "اختر بنفسك أحد الدومينات المجانية المتاحة\n"
            "لإنشاء الإيميل عليه."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎲 إنشاء سريع",
                        callback_data="create_email_fast",
                        style="success",
                    ),
                    InlineKeyboardButton(
                        "🌐 اختيار الدومين",
                        callback_data="select_free_domain",
                        style="primary",
                    ),
                ],
                [InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="back_to_menu",
                    style="primary",
                )],
            ]),
        )
        return

    if data == "create_email_fast":
        current_count = len(get_user_emails(user_id))
        email_limit = get_effective_email_limit(user_id)
        if (not is_admin(user_id)) and email_limit > 0 and current_count >= email_limit:
            await query.edit_message_text(
                "⚠️ لقد وصلت إلى الحد المسموح لإنشاء الإيميلات.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
            )
            return

        await query.edit_message_text(
            "🎲 إنشاء سريع\n\n"
            "جاري إنشاء إيميل جديد باستخدام أحد الدومينات المجانية المتاحة..."
        )
        email, token, password = await asyncio.to_thread(create_email)
        if email and token:
            add_user_email(user_id, email, token, password)
            await asyncio.to_thread(increment_daily_stat, "emails_created")
            await query.edit_message_text(
                get_text(lang, "email_created", email=telegram_html(email)),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                get_text(lang, "error_create_email"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 إعادة المحاولة",
                        callback_data="create_email_fast",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )],
                ]),
            )
        return

    if data in ("select_free_domain", "refresh_free_domains"):
        domains = await asyncio.to_thread(get_available_domains)
        if not domains:
            context.user_data.pop("free_domains", None)
            await query.edit_message_text(
                "🌐 اختيار الدومين\n\n"
                "تعذر تحميل الدومينات المجانية المتاحة حالياً.\n"
                "حاول التحديث بعد قليل.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 تحديث الدومينات",
                        callback_data="refresh_free_domains",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )],
                ]),
            )
            return

        context.user_data["free_domains"] = list(domains)
        await query.edit_message_text(
            "🌐 اختيار الدومين\n\n"
            "اختر أحد الدومينات المجانية المتاحة أدناه.\n"
            "بعد اختيار الدومين سيتم إنشاء إيميل جديد\n"
            "تلقائياً عليه.",
            reply_markup=get_free_domains_keyboard(domains),
        )
        return

    if re.fullmatch(r"free_domain_\\d+", data):
        domain_index = int(data.rsplit("_", 1)[1])
        domains = list(context.user_data.get("free_domains") or [])
        if domain_index >= len(domains):
            domains = await asyncio.to_thread(get_available_domains)
            context.user_data["free_domains"] = list(domains)
            await query.edit_message_text(
                "🌐 اختيار الدومين\n\n"
                "تم تحديث قائمة الدومينات. اختر الدومين من جديد.",
                reply_markup=(
                    get_free_domains_keyboard(domains)
                    if domains
                    else InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "🔄 تحديث الدومينات",
                            callback_data="refresh_free_domains",
                            style="success",
                        )
                    ], [
                        InlineKeyboardButton(
                            get_text(lang, "btn_back"),
                            callback_data="create_email",
                            style="primary",
                        )
                    ]])
                ),
            )
            return

        current_count = len(get_user_emails(user_id))
        email_limit = get_effective_email_limit(user_id)
        if (not is_admin(user_id)) and email_limit > 0 and current_count >= email_limit:
            await query.edit_message_text(
                "⚠️ لقد وصلت إلى الحد المسموح لإنشاء الإيميلات.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
            )
            return

        domain = domains[domain_index]
        await query.edit_message_text(
            "🌐 إنشاء الإيميل\n\n"
            f"جاري إنشاء إيميل جديد على الدومين:\n@{domain}"
        )
        email, token, password = await asyncio.to_thread(create_email_with_domain, domain)
        if email and token:
            add_user_email(user_id, email, token, password)
            await asyncio.to_thread(increment_daily_stat, "emails_created")
            await query.edit_message_text(
                get_text(lang, "email_created", email=telegram_html(email)),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="back_to_menu",
                        style="primary",
                    )
                ]]),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                "❌ فشل إنشاء الإيميل على الدومين المحدد.\n\n"
                "قد يكون الدومين لم يعد متاحاً، حدّث القائمة وحاول مرة أخرى.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 تحديث الدومينات",
                        callback_data="refresh_free_domains",
                        style="success",
                    )],
                    [InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="create_email",
                        style="primary",
                    )],
                ]),
            )
        return

'''
text = text[:start] + new_block + text[end:]

# فحص نحوي بدون إنشاء __pycache__.
compile(text, "telegram_bot.py", "exec")

# مزامنة ملف التصدير مع الحفاظ على مقدمته الحالية.
export_path = Path("bot_code_export.txt")
export_text = export_path.read_text(encoding="utf-8")
marker = "================================================================================\n📁 telegram_bot.py\n================================================================================\n"
if marker not in export_text:
    raise SystemExit("telegram_bot.py export marker not found")
prefix = export_text.split(marker, 1)[0] + marker
export_path.write_text(prefix + text, encoding="utf-8")
path.write_text(text, encoding="utf-8")

checks = [
    'callback_data="create_email_fast"',
    'callback_data="select_free_domain"',
    'callback_data="refresh_free_domains"',
    'def create_email_with_domain(domain):',
    'def get_free_domains_keyboard(domains):',
    'اختر بنفسك أحد الدومينات المجانية المتاحة',
    'بعد اختيار الدومين سيتم إنشاء إيميل جديد',
]
for check in checks:
    if check not in text:
        raise SystemExit(f"Missing expected change: {check}")
