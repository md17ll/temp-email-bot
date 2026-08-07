from pathlib import Path
import ast
import re

path = Path("telegram_bot.py")
source = path.read_text(encoding="utf-8")
original_callbacks = set(re.findall(r'callback_data="([^"]+)"', source))


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


# التاريخ مطلوب لإكمال الأيام التي لا تحتوي على استخدام ضمن آخر 7 أيام.
replace_once(
    "from html import escape, unescape\n",
    "from datetime import timedelta\nfrom html import escape, unescape\n",
)

# جدول مستقل وخفيف للإحصائيات اليومية؛ لا يغير جداول المستخدمين الحالية.
replace_once(
    '''            # الواجهة أصبحت عربية فقط؛ توحيد بيانات المستخدمين القديمة دون تغيير بنية الجدول.\n''',
    '''            # إحصائيات الاستخدام اليومية.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage_daily_stats (
                    stat_date DATE PRIMARY KEY DEFAULT CURRENT_DATE,
                    new_users BIGINT NOT NULL DEFAULT 0,
                    emails_created BIGINT NOT NULL DEFAULT 0,
                    inbox_opens BIGINT NOT NULL DEFAULT 0
                )
            """)

            # الواجهة أصبحت عربية فقط؛ توحيد بيانات المستخدمين القديمة دون تغيير بنية الجدول.
''',
)

# دوال تسجيل وقراءة الإحصائيات اليومية بعمليات ذرية داخل PostgreSQL.
replace_once(
    '''def get_email_limit() -> int:\n''',
    '''def increment_daily_stat(stat_name: str) -> bool:
    """زيادة عداد يومي واحد بشكل ذري وآمن مع تعدد المستخدمين."""
    allowed = {"new_users", "emails_created", "inbox_opens"}
    if stat_name not in allowed:
        return False

    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO usage_daily_stats(stat_date, {stat_name})
                VALUES (CURRENT_DATE, 1)
                ON CONFLICT(stat_date)
                DO UPDATE SET {stat_name} = usage_daily_stats.{stat_name} + 1
                """
            )
            conn.commit()
            return True
    except Exception as error:
        print(f"⚠️ خطأ في تسجيل إحصائية {stat_name}: {error}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_last_seven_days_usage():
    """إرجاع آخر 7 أيام بما فيها الأيام التي لم يحدث فيها استخدام."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT CURRENT_DATE AS today")
            today = cur.fetchone()["today"]
            cur.execute("""
                SELECT stat_date, new_users, emails_created, inbox_opens
                FROM usage_daily_stats
                WHERE stat_date BETWEEN CURRENT_DATE - INTERVAL '6 days' AND CURRENT_DATE
                ORDER BY stat_date DESC
            """)
            rows = cur.fetchall()

        by_date = {row["stat_date"]: row for row in rows}
        result = []
        for offset in range(7):
            day = today - timedelta(days=offset)
            row = by_date.get(day) or {}
            result.append({
                "stat_date": day,
                "new_users": int(row.get("new_users") or 0),
                "emails_created": int(row.get("emails_created") or 0),
                "inbox_opens": int(row.get("inbox_opens") or 0),
            })
        return result
    except Exception as error:
        print(f"⚠️ خطأ في قراءة الإحصائيات اليومية: {error}")
        return []
    finally:
        conn.close()


def get_email_limit() -> int:
''',
)

# تسجيل المستخدم الجديد مرة واحدة عند أول ظهور له.
replace_once(
    '''    if is_new:\n        await notify_admin_new_user(context, user)\n''',
    '''    if is_new:
        await asyncio.to_thread(increment_daily_stat, "new_users")
        await notify_admin_new_user(context, user)
''',
)

# مساعد بصري موحد: زران بكل صف، والزر الأخير بعرض كامل إن كان منفرداً، ثم رجوع كامل.
replace_once(
    '''def get_admin_panel_keyboard(_lang, user_id):\n''',
    '''def get_admin_section_keyboard(buttons, back_callback="admin_panel"):
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data=back_callback,
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_panel_keyboard(_lang, user_id):
''',
)

# إدارة القنوات بنفس شكل الصورة: خياران في كل صف والرجوع كامل.
replace_between(
    "def get_channel_management_keyboard(_lang):\n",
    "# ================== أدوات منع/سماح (جديد) ==================\n",
    '''def get_channel_management_keyboard(_lang):
    channel_info = get_channel_info(only_enabled=False)
    buttons = [
        InlineKeyboardButton("تعيين القناة", callback_data="set_channel", style="primary"),
        InlineKeyboardButton("تعيين رسالة الاشتراك", callback_data="set_channel_message", style="primary"),
    ]
    if channel_info:
        status_icon = "✅" if channel_info.get("subscription_enabled") else "❌"
        buttons.extend([
            InlineKeyboardButton(
                f"إشعار الاشتراك: {status_icon}",
                callback_data="toggle_subscription",
                style="success" if channel_info.get("subscription_enabled") else "danger",
            ),
            InlineKeyboardButton("حذف القناة", callback_data="delete_channel", style="danger"),
        ])
    return get_admin_section_keyboard(buttons, "admin_panel")

# ================== أدوات منع/سماح (جديد) ==================
''',
)

# قسم الإحصائيات يصبح مدخلاً بخيارين شفافين؛ الإحصائيات العامة تبقى كما كانت.
replace_between(
    '    if data == "section_stats":\n',
    '    if data == "section_forward":\n',
    '''    if data == "section_stats":
        if not is_admin(user_id):
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📊 الإحصائيات العامة",
                    callback_data="stats_general",
                    transparent=True,
                ),
                InlineKeyboardButton(
                    "📈 إحصائيات الاستخدام اليومية",
                    callback_data="stats_daily",
                    transparent=True,
                ),
            ],
            [InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="admin_panel",
                style="primary",
            )],
        ])
        await query.edit_message_text(
            "📊 قسم الإحصائيات\n\nاختر نوع الإحصائيات التي تريد عرضها:",
            reply_markup=kb,
        )
        return

    if data == "stats_general":
        if not is_admin(user_id):
            return
        total_users = len(user_database)
        total_emails = sum(len(u.get("emails", [])) for u in user_database.values())
        active_users = sum(1 for u in user_database.values() if len(u.get("emails", [])) > 0)
        text = (
            "📊 الإحصائيات العامة\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"📧 إجمالي الإيميلات: {total_emails}\n"
            f"🔄 المستخدمون النشطون: {active_users}\n"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="section_stats",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "stats_daily":
        if not is_admin(user_id):
            return
        days = await asyncio.to_thread(get_last_seven_days_usage)
        if not days:
            text = "📈 إحصائيات الاستخدام اليومية\n\n❌ تعذر قراءة الإحصائيات حالياً."
        else:
            today = days[0]
            labels = ["اليوم", "أمس"]
            day_lines = []
            for index, item in enumerate(days):
                if index < len(labels):
                    label = labels[index]
                else:
                    label = item["stat_date"].strftime("%Y-%m-%d")
                day_lines.append(f"📅 {label}: {item['emails_created']} إيميل")

            best_day = max(days, key=lambda item: item["emails_created"])
            if best_day["stat_date"] == days[0]["stat_date"]:
                best_label = "اليوم"
            elif len(days) > 1 and best_day["stat_date"] == days[1]["stat_date"]:
                best_label = "أمس"
            else:
                best_label = best_day["stat_date"].strftime("%Y-%m-%d")

            text = (
                "📈 إحصائيات الاستخدام اليومية\n\n"
                "📅 إحصائيات اليوم:\n\n"
                f"👤 المستخدمون الجدد: {today['new_users']}\n"
                f"📧 الإيميلات المنشأة: {today['emails_created']}\n"
                f"📥 مرات فتح صندوق الوارد: {today['inbox_opens']}\n\n"
                "━━━━━━━━━━━━━━\n\n"
                "📊 آخر 7 أيام:\n\n"
                + "\n".join(day_lines)
                + "\n\n"
                "🏆 أعلى يوم استخدام خلال آخر 7 أيام:\n"
                f"{best_label}: {best_day['emails_created']} إيميل"
            )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 تحديث الإحصائيات",
                    callback_data="stats_daily",
                    style="success",
                )],
                [InlineKeyboardButton(
                    "🔙 رجوع إلى قسم الإحصائيات",
                    callback_data="section_stats",
                    style="primary",
                )],
            ]),
        )
        return

''',
)

# توجيه الرسائل: الخياران جنب بعض.
replace_between(
    '    if data == "section_forward":\n',
    '    if data == "forward_on":\n',
    '''    if data == "section_forward":
        if not is_admin(user_id):
            return
        status = "✅ مفعّل" if forwarding_enabled else "❌ معطّل"
        text = f"📨 قسم توجيه الرسائل\n\nالحالة: {status}\n\nعند التفعيل، أي رسالة يرسلها المستخدمون ستصلك مباشرة."
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("✅ تفعيل التوجيه", callback_data="forward_on", style="success"),
            InlineKeyboardButton("❌ تعطيل التوجيه", callback_data="forward_off", style="danger"),
        ], "admin_panel")
        await query.edit_message_text(text, reply_markup=kb)
        return

''',
)

# الإعدادات: الخياران جنب بعض.
replace_between(
    '    if data == "section_settings":\n',
    '    if data == "toggle_bot_status":\n',
    '''    if data == "section_settings":
        if not is_admin(user_id):
            return
        status_icon = "✅" if bot_active else "❌"
        status_text = "يعمل" if bot_active else "متوقف"
        text = f"⚙️ الإعدادات\n\n• حالة البوت: {status_icon} {status_text}\n"
        if not bot_active and bot_offline_message:
            text += f"• رسالة الإيقاف: {bot_offline_message[:80]}..."
        kb = get_admin_section_keyboard([
            InlineKeyboardButton(f"🔄 حالة البوت: {status_icon}", callback_data="toggle_bot_status"),
            InlineKeyboardButton("✏️ رسالة الإيقاف", callback_data="set_offline_message"),
        ], "admin_panel")
        await query.edit_message_text(text, reply_markup=kb)
        return

''',
)

# الإذاعة: الخياران جنب بعض.
replace_between(
    '    if data == "section_broadcast":\n',
    '    if data == "broadcast_all":\n',
    '''    if data == "section_broadcast":
        if not is_admin(user_id):
            return
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("📨 إذاعة للكل", callback_data="broadcast_all", style="primary"),
            InlineKeyboardButton("👥 إذاعة للنشطين فقط", callback_data="broadcast_active", style="primary"),
        ], "admin_panel")
        await query.edit_message_text("📢 قسم الإذاعة\n\nاختر نوع الإذاعة:", reply_markup=kb)
        return

''',
)

# الدومينات المدفوعة: الإضافة والحذف في صف واحد عند توفر الحذف.
replace_between(
    '    if data == "section_paid_domains":\n',
    '    if data == "add_paid_domain":\n',
    '''    if data == "section_paid_domains":
        if not is_admin(user_id):
            return

        domains = get_paid_domains()
        text = "🌐 إدارة الدومينات المدفوعة\n\n"
        if domains:
            text += "الدومينات المعروضة للمستخدمين:\n"
            for index, domain in enumerate(domains, start=1):
                text += f"{index}. @{domain}\n"
        else:
            text += "لا توجد دومينات مضافة حالياً."

        buttons = [
            InlineKeyboardButton("➕ إضافة دومين", callback_data="add_paid_domain", style="success"),
        ]
        if domains:
            buttons.append(
                InlineKeyboardButton("🗑️ حذف دومين", callback_data="delete_paid_domain", style="danger")
            )
        await query.edit_message_text(
            text,
            reply_markup=get_admin_section_keyboard(buttons, "admin_panel"),
        )
        return

''',
)

# قائمة حذف الدومينات نفسها صفّان صفّان.
replace_between(
    '    if data == "delete_paid_domain":\n',
    '    if re.fullmatch(r"remove_paid_domain_\\d+", data):\n',
    '''    if data == "delete_paid_domain":
        if not is_admin(user_id):
            return
        domains = get_paid_domains()
        if not domains:
            await query.edit_message_text(
                "❌ لا توجد دومينات للحذف.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="section_paid_domains",
                        style="primary",
                    )
                ]]),
            )
            return

        buttons = [
            InlineKeyboardButton(
                f"🗑️ @{domain}",
                callback_data=f"remove_paid_domain_{index}",
                style="danger",
            )
            for index, domain in enumerate(domains)
        ]
        await query.edit_message_text(
            "🗑️ اختر الدومين الذي تريد حذفه:",
            reply_markup=get_admin_section_keyboard(buttons, "section_paid_domains"),
        )
        return

''',
)

# حد الإيميلات: نفس الخيارات، مرتبة زرين في كل صف.
replace_between(
    '    if data == "section_email_limit":\n',
    '    if data == "set_email_limit":\n',
    '''    if data == "section_email_limit":
        if not is_admin(user_id):
            return
        limit = get_email_limit()
        current = "غير محدود" if limit == 0 else str(limit)
        contact_username = get_admin_contact_username()
        contact_text = f"@{contact_username}" if contact_username else "غير محدد"
        buttons = [
            InlineKeyboardButton("✏️ تحديد العدد", callback_data="set_email_limit", style="primary"),
        ]
        if user_id == ADMIN_ID:
            buttons.append(
                InlineKeyboardButton(
                    "🎯 تحديد حد عضو عبر ID",
                    callback_data="set_member_email_limit",
                    style="primary",
                )
            )
        buttons.extend([
            InlineKeyboardButton(
                "👤 إضافة يوزر التواصل",
                callback_data="set_admin_contact_username",
                transparent=True,
            ),
            InlineKeyboardButton("♾️ إلغاء الحد", callback_data="clear_email_limit", style="danger"),
        ])
        await query.edit_message_text(
            "🔢 حد إنشاء الإيميلات\n\n"
            f"الحد الحالي لكل مستخدم: {current}\n"
            f"يوزر التواصل مع الأدمن: {contact_text}",
            reply_markup=get_admin_section_keyboard(buttons, "admin_panel"),
        )
        return

''',
)

# إدارة الأعضاء: أربعة خيارات على صفين، والخيار الخامس إن وجد بعرض كامل.
replace_between(
    '    if data == "section_members":\n',
    '    if data == "users_list_all" or re.fullmatch(r"users_list_all_\\d+", data):\n',
    '''    if data == "section_members":
        if not is_admin(user_id):
            return
        total_users = len(user_database)
        active_users = sum(1 for u in user_database.values() if len(u.get("emails", [])) > 0)
        inactive_users = total_users - active_users
        total_emails = sum(len(u.get("emails", [])) for u in user_database.values())
        text = (
            "👥 إدارة الأعضاء\n\n"
            f"• إجمالي الأعضاء: {total_users}\n"
            f"• الأعضاء النشطون: {active_users}\n"
            f"• الأعضاء غير النشطين: {inactive_users}\n"
            f"• إجمالي الإيميلات: {total_emails}\n"
        )
        buttons = [
            InlineKeyboardButton("📋 قائمة كل الأعضاء", callback_data="users_list_all", style="primary"),
            InlineKeyboardButton("✅ الأعضاء النشطين", callback_data="users_list_active", style="success"),
            InlineKeyboardButton("🏆 الأكثر إيميلات", callback_data="users_list_top", style="primary"),
            InlineKeyboardButton("🔍 بحث عن عضو", callback_data="search_member", style="primary"),
        ]
        if user_id == ADMIN_ID:
            buttons.append(
                InlineKeyboardButton(
                    "🗑️ حذف إيميلات عضو",
                    callback_data="delete_user_emails",
                    style="danger",
                )
            )
        await query.edit_message_text(
            text,
            reply_markup=get_admin_section_keyboard(buttons, "admin_panel"),
        )
        return

''',
)

# إدارة المشرفين: إضافة وإزالة جنب بعض.
replace_between(
    '    if data == "section_admins":\n',
    '    if data == "add_admin":\n',
    '''    if data == "section_admins":
        if not is_admin(user_id):
            return
        if user_id != ADMIN_ID:
            await query.answer("هذا القسم للمشرف الرئيسي فقط!", show_alert=True)
            return

        admins = get_all_admins()
        text = "👮 إدارة المشرفين\n━━━━━━━━━━━━━━━\n\n"
        text += f"👑 المشرف الرئيسي: <code>{ADMIN_ID}</code>\n\n"
        if admins:
            text += f"👮 المشرفون الإضافيون ({len(admins)}):\n"
            for a in admins:
                name = a.get("first_name") or "مجهول"
                username = f"@{a.get('username')}" if a.get("username") else "—"
                text += f"• {telegram_html(name)} | {telegram_html(username)}\n  ID: <code>{a['telegram_id']}</code>\n"
        else:
            text += "لا يوجد مشرفون إضافيون حالياً\n"

        kb = get_admin_section_keyboard([
            InlineKeyboardButton("➕ إضافة مشرف", callback_data="add_admin", style="success"),
            InlineKeyboardButton("➖ إزالة مشرف", callback_data="remove_admin", style="danger"),
        ], "admin_panel")
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        return

''',
)

# قائمة إزالة المشرفين صفّان صفّان.
replace_once(
    '''        kb_rows = []
        for a in admins:
            name = a.get("first_name") or str(a["telegram_id"])
            kb_rows.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"confirm_remove_admin_{a['telegram_id']}", style="danger")])
        kb_rows.append([InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="section_admins")])
        await query.edit_message_text("➖ اختر المشرف لإزالته:", reply_markup=InlineKeyboardMarkup(kb_rows))
''',
    '''        buttons = []
        for a in admins:
            name = a.get("first_name") or str(a["telegram_id"])
            buttons.append(
                InlineKeyboardButton(
                    f"❌ {name}",
                    callback_data=f"confirm_remove_admin_{a['telegram_id']}",
                    style="danger",
                )
            )
        await query.edit_message_text(
            "➖ اختر المشرف لإزالته:",
            reply_markup=get_admin_section_keyboard(buttons, "section_admins"),
        )
''',
)

# الحظر: حظر وفك حظر جنب بعض.
replace_between(
    '    if data == "section_ban":\n',
    '    if data == "ban_user":\n',
    '''    if data == "section_ban":
        if not is_admin(user_id):
            return
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("🛑 حظر مستخدم", callback_data="ban_user", style="danger"),
            InlineKeyboardButton("✅ فك حظر مستخدم", callback_data="unban_user", style="success"),
        ], "admin_panel")
        await query.edit_message_text("🛑 قسم الحظر\n\nاختر:", reply_markup=kb)
        return

''',
)

# الترحيب: تعيين وحذف جنب بعض.
replace_between(
    '    if data == "section_welcome":\n',
    '    if data == "set_welcome_message":\n',
    '''    if data == "section_welcome":
        if not is_admin(user_id):
            return
        current = get_setting("welcome_message", "")
        kb = get_admin_section_keyboard([
            InlineKeyboardButton("✏️ تعيين رسالة الترحيب", callback_data="set_welcome_message", style="success"),
            InlineKeyboardButton("🧹 حذف رسالة الترحيب", callback_data="clear_welcome_message", style="danger"),
        ], "admin_panel")
        text = "👋 رسالة الترحيب الحالية:\n\n"
        text += (current if current else "— لا توجد رسالة —")
        await query.edit_message_text(text, reply_markup=kb)
        return

''',
)

# تسجيل إنشاء الإيميل الناجح.
replace_once(
    '''            add_user_email(user_id, email, token, password)\n            await query.edit_message_text(\n''',
    '''            add_user_email(user_id, email, token, password)
            await asyncio.to_thread(increment_daily_stat, "emails_created")
            await query.edit_message_text(
''',
)

# تسجيل فتح/تحديث صندوق وارد صالح بعد التأكد من وجود الإيميل.
replace_once(
    '''        email_data = emails[email_index]\n        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, user_id, email_index)\n''',
    '''        email_data = emails[email_index]
        await asyncio.to_thread(increment_daily_stat, "inbox_opens")
        inbox_result = await asyncio.to_thread(check_user_inbox_detailed, user_id, email_index)
''',
)

# لا نحذف أي callback قديم؛ فقط نضيف callbacks الإحصائيات الجديدة.
new_callbacks = set(re.findall(r'callback_data="([^"]+)"', source))
missing_callbacks = sorted(original_callbacks - new_callbacks)
if missing_callbacks:
    raise RuntimeError(f"Existing callback_data values were removed: {missing_callbacks}")

required = (
    'callback_data="stats_general"',
    'callback_data="stats_daily"',
    'transparent=True',
    'CREATE TABLE IF NOT EXISTS usage_daily_stats',
    'increment_daily_stat, "new_users"',
    'increment_daily_stat, "emails_created"',
    'increment_daily_stat, "inbox_opens"',
    'def get_admin_section_keyboard(',
)
for marker in required:
    if marker not in source:
        raise RuntimeError(f"Missing required marker: {marker}")

# صندوق الوارد يبقى يدوياً بالكامل.
if "application.job_queue" in source or "run_repeating" in source:
    raise RuntimeError("Unexpected background inbox polling was introduced")

ast.parse(source)
path.write_text(source, encoding="utf-8")

# إبقاء ملف التصدير مطابقاً للكود التشغيلي كما كان معمولاً به في المشروع.
export_path = Path("bot_code_export.txt")
export_text = export_path.read_text(encoding="utf-8")
marker = "#!/usr/bin/env python3"
marker_index = export_text.find(marker)
if marker_index == -1:
    raise RuntimeError("telegram_bot.py marker not found in bot_code_export.txt")
export_path.write_text(export_text[:marker_index] + source, encoding="utf-8")
