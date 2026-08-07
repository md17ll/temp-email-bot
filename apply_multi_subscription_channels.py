from pathlib import Path
import ast
import re

path = Path("telegram_bot.py")
source = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match, found {count}: {old[:160]!r}")
    source = source.replace(old, new, 1)


def replace_between(start_marker: str, end_marker: str, replacement: str) -> None:
    global source
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start == -1 or end == -1:
        raise RuntimeError(f"Markers not found: {start_marker!r} -> {end_marker!r}")
    source = source[:start] + replacement + source[end:]


# استبدال منطق القنوات فقط؛ جدول channels الحالي يدعم عدة صفوف أصلاً.
replace_between(
    "# ================== إدارة القنوات (مثل كودك) ==================\n",
    "# ================== mail.tm API ==================\n",
    r'''# ================== إدارة القنوات (اشتراك إجباري متعدد) ==================

def get_channels(only_enabled=True):
    """جلب كل قنوات الاشتراك، مع الحفاظ على ترتيب إضافتها."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if only_enabled:
                cur.execute("""
                    SELECT id, channel_username, channel_id, channel_title,
                           subscription_message, subscription_enabled, created_at
                    FROM channels
                    WHERE subscription_enabled = TRUE
                    ORDER BY created_at ASC, id ASC
                """)
            else:
                cur.execute("""
                    SELECT id, channel_username, channel_id, channel_title,
                           subscription_message, subscription_enabled, created_at
                    FROM channels
                    ORDER BY created_at ASC, id ASC
                """)
            return cur.fetchall()
    except Exception as e:
        print(f"❌ خطأ في الحصول على قائمة القنوات: {e}")
        return []
    finally:
        conn.close()


def get_channel_by_id(channel_db_id: int):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, channel_username, channel_id, channel_title,
                       subscription_message, subscription_enabled, created_at
                FROM channels
                WHERE id=%s
                LIMIT 1
            """, (int(channel_db_id),))
            return cur.fetchone()
    except Exception as e:
        print(f"❌ خطأ في جلب القناة: {e}")
        return None
    finally:
        conn.close()


def get_channel_info(only_enabled=True):
    """للتوافق مع الأجزاء القديمة: يرجع قناة واحدة فقط عند الحاجة."""
    channels = get_channels(only_enabled=only_enabled)
    if not channels:
        return None
    return channels[0] if only_enabled else channels[-1]


def set_channel(channel_username, channel_id=None, channel_title=None):
    """إضافة قناة جديدة أو تحديث القناة نفسها بدون حذف القنوات الأخرى."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channels (channel_username, channel_id, channel_title, subscription_enabled)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (channel_username)
                DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    channel_title = EXCLUDED.channel_title,
                    subscription_enabled = TRUE,
                    updated_at = CURRENT_TIMESTAMP
            """, (channel_username, channel_id, channel_title))
            conn.commit()
            return True
    except Exception as e:
        print(f"❌ خطأ في إضافة القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def set_channel_message(channel_username, message):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM channels WHERE channel_username=%s", (channel_username,))
            if not cur.fetchone():
                return False
            cur.execute("""
                UPDATE channels
                SET subscription_message=%s, updated_at=CURRENT_TIMESTAMP
                WHERE channel_username=%s
            """, (message, channel_username))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"❌ خطأ في تعيين رسالة القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_channel(channel_username):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM channels WHERE channel_username=%s", (channel_username,))
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    except Exception as e:
        print(f"❌ خطأ في حذف القناة: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def toggle_subscription(channel_username):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channels
                SET subscription_enabled = NOT subscription_enabled, updated_at=CURRENT_TIMESTAMP
                WHERE channel_username=%s
                RETURNING subscription_enabled
            """, (channel_username,))
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else False
    except Exception as e:
        print(f"❌ خطأ في تبديل حالة الاشتراك: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


# ================== اشتراك إجباري متعدد ==================

async def get_missing_subscription_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """يرجع القنوات المفعّلة التي لم يشترك بها العضو بعد."""
    missing = []
    channels = get_channels(only_enabled=True)

    for channel_info in channels:
        channel_username = channel_info["channel_username"]
        channel_id = channel_info.get("channel_id")
        chat_identifier = channel_id if channel_id else f"@{channel_username}"
        subscribed = False
        temporary_failure = False

        for attempt in range(2):
            try:
                member = await context.bot.get_chat_member(chat_identifier, user_id)
                subscribed = member.status in ("member", "administrator", "creator")
                break
            except Exception as error:
                error_text = str(error).lower()
                temporary_failure = any(term in error_text for term in (
                    "readerror", "timeout", "timed out", "network", "connection",
                    "bad gateway", "temporarily unavailable", "server error",
                ))
                if temporary_failure and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue

                print(
                    f"⚠️ فشل فحص اشتراك المستخدم {user_id} في @{channel_username}: "
                    f"{type(error).__name__}: {error}"
                )
                break

        # عطل الشبكة المؤقت لا يمنع المستخدم، مثل السلوك السابق.
        if temporary_failure and not subscribed:
            continue
        if not subscribed:
            missing.append(channel_info)

    return missing


async def check_user_subscription_strict(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يجب أن يكون العضو مشتركاً بكل القنوات المفعّلة."""
    missing = await get_missing_subscription_channels(user_id, context)
    return len(missing) == 0


def subscription_prompt(_lang: str, channels, message: str = ""):
    """عرض كل القنوات الناقصة بأزرار انضمام منفصلة ثم زر تحقق واحد."""
    if isinstance(channels, str):
        channels = [{
            "channel_username": channels,
            "channel_title": channels,
            "subscription_message": message,
        }]
    channels = list(channels or [])

    text_lines = [
        "⚠️ يجب عليك الاشتراك في القنوات التالية لاستخدام البوت:",
        "",
    ]
    for index, channel in enumerate(channels, start=1):
        username = str(channel.get("channel_username") or "").lstrip("@")
        title = str(channel.get("channel_title") or username)
        text_lines.append(f"{index}. 📢 {title} — @{username}")
        custom_message = str(channel.get("subscription_message") or "").strip()
        if custom_message:
            text_lines.append(f"   {custom_message}")

    text_lines.extend([
        "",
        "بعد الاشتراك في جميع القنوات اضغط: ✅ التحقق من الاشتراك",
    ])

    rows = []
    for channel in channels:
        username = str(channel.get("channel_username") or "").lstrip("@")
        title = str(channel.get("channel_title") or username)
        display_title = title if len(title) <= 28 else title[:25] + "..."
        rows.append([
            InlineKeyboardButton(
                f"📢 الانضمام: {display_title}",
                url=f"https://t.me/{username}",
                style="primary",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "✅ التحقق من الاشتراك",
            callback_data="verify_subscription",
            style="success",
        )
    ])
    return "\n".join(text_lines), InlineKeyboardMarkup(rows)

# ================== mail.tm API ==================
''',
)

# واجهة إدارة القنوات: إضافة أكثر من قناة ثم اختيار أي قناة لإدارتها.
replace_between(
    "def get_channel_management_keyboard(_lang):\n",
    "# ================== أدوات منع/سماح (جديد) ==================\n",
    r'''def get_channel_management_keyboard(_lang):
    channels = get_channels(only_enabled=False)
    rows = [[
        InlineKeyboardButton(
            "➕ إضافة قناة",
            callback_data="set_channel",
            style="success",
        )
    ]]

    channel_buttons = []
    for channel in channels:
        status_icon = "✅" if channel.get("subscription_enabled") else "❌"
        username = str(channel.get("channel_username") or "")
        channel_buttons.append(
            InlineKeyboardButton(
                f"{status_icon} @{username}",
                callback_data=f"manage_channel_{channel['id']}",
                style="primary",
            )
        )
    rows.extend([
        channel_buttons[index:index + 2]
        for index in range(0, len(channel_buttons), 2)
    ])
    rows.append([
        InlineKeyboardButton(
            get_text("ar", "btn_back"),
            callback_data="admin_panel",
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(rows)


# ================== أدوات منع/سماح (جديد) ==================
''',
)

# الحارس: يطالب بكل القنوات الناقصة، وعند اكتمالها يسجل إشعار كل قناة مرة واحدة.
replace_once(
    r'''    # اشتراك صارم (لغير الأدمن)
    if not admin_user:
        ok = await check_user_subscription_strict(user_id, context)
        if not ok:
            ch = get_channel_info()
            if ch:
                msg = ch.get("subscription_message") or ""
                text, kb = subscription_prompt(lang, ch["channel_username"], msg)
                if hasattr(update_or_query, "message") and update_or_query.message:
                    await update_or_query.message.reply_text(text, reply_markup=kb)
                else:
                    try:
                        await update_or_query.edit_message_text(text, reply_markup=kb)
                    except:
                        pass
            return False

        active_channel = get_channel_info()
        if active_channel:
            await notify_admin_subscription(context, user_id, active_channel)
''',
    r'''    # اشتراك صارم بكل القنوات المفعّلة (لغير الأدمن)
    if not admin_user:
        active_channels = get_channels(only_enabled=True)
        missing_channels = await get_missing_subscription_channels(user_id, context)
        if missing_channels:
            text, kb = subscription_prompt(lang, missing_channels)
            if hasattr(update_or_query, "message") and update_or_query.message:
                await update_or_query.message.reply_text(text, reply_markup=kb)
            else:
                try:
                    await update_or_query.edit_message_text(text, reply_markup=kb)
                except Exception:
                    pass
            return False

        for active_channel in active_channels:
            await notify_admin_subscription(context, user_id, active_channel)
''',
)

# زر التحقق يعيد عرض القنوات التي ما زالت ناقصة فقط.
replace_between(
    '    # ✅ تحقق الاشتراك (زر)\n    if data == "verify_subscription":\n',
    '    # ================== لوحة الأدمن (القديمة) ==================\n',
    r'''    # ✅ تحقق الاشتراك (زر)
    if data == "verify_subscription":
        missing_channels = await get_missing_subscription_channels(user_id, context)
        if not missing_channels:
            for active_channel in get_channels(only_enabled=True):
                await notify_admin_subscription(context, user_id, active_channel)
            text = "✅ تم التحقق من اشتراكك في جميع القنوات بنجاح!\n\n" + build_main_menu_text(user_id)
            await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang, user_id))
        else:
            text, kb = subscription_prompt(lang, missing_channels)
            await query.edit_message_text(text, reply_markup=kb)
        return

    # ================== لوحة الأدمن (القديمة) ==================
''',
)

# واجهة وقواعد إدارة كل قناة على حدة، مع إبقاء callbacks القديمة للتوافق مع الرسائل القديمة.
replace_between(
    '    if data == "channel_management":\n',
    '    # أقسام الأدمن القديمة الأساسية (موجودة ومفعلة)\n',
    r'''    if data == "channel_management":
        if not is_admin(user_id):
            await query.answer(get_text(lang, "unauthorized"), show_alert=True)
            return

        channels = get_channels(only_enabled=False)
        enabled_count = sum(1 for item in channels if item.get("subscription_enabled"))
        text = (
            "📢 إدارة قنوات الاشتراك الإجباري\n\n"
            f"📋 عدد القنوات المضافة: {len(channels)}\n"
            f"✅ القنوات المفعّلة: {enabled_count}\n\n"
        )
        if channels:
            text += "اضغط على أي قناة لإدارتها، أو أضف قناة جديدة."
        else:
            text += "لا توجد قنوات حالياً. أضف أول قناة للبدء."

        await query.edit_message_text(
            text,
            reply_markup=get_channel_management_keyboard(lang),
        )
        return

    if data == "set_channel":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = "channel_username"
        await query.edit_message_text(
            "➕ إضافة قناة للاشتراك الإجباري\n\n"
            "أرسل username القناة بدون @.\n"
            "مثال: mychannel\n\n"
            "يمكنك إضافة أكثر من قناة، ولن تُحذف القنوات السابقة.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
        )
        return

    if re.fullmatch(r"manage_channel_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if not channel_info:
            await query.edit_message_text(
                "❌ هذه القناة لم تعد موجودة.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
            return

        status = "✅ مفعّل" if channel_info.get("subscription_enabled") else "❌ معطّل"
        status_button = "❌ تعطيل الاشتراك" if channel_info.get("subscription_enabled") else "✅ تفعيل الاشتراك"
        status_style = "danger" if channel_info.get("subscription_enabled") else "success"
        msg = channel_info.get("subscription_message") or "لا توجد رسالة خاصة"
        cid = channel_info.get("channel_id", "غير محدد")
        title = channel_info.get("channel_title", "غير محدد")
        username = channel_info["channel_username"]
        text = (
            "📢 إدارة القناة\n\n"
            f"📢 الاسم: <b>{telegram_html(title)}</b>\n"
            f"🔗 القناة: @{telegram_html(username)}\n"
            f"🆔 المعرّف: <code>{cid}</code>\n"
            f"⚙️ الحالة: {status}\n"
            f"📝 رسالة الاشتراك: {telegram_html(msg)}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📝 رسالة الاشتراك",
                    callback_data=f"set_channel_message_{channel_db_id}",
                    style="primary",
                ),
                InlineKeyboardButton(
                    status_button,
                    callback_data=f"toggle_subscription_{channel_db_id}",
                    style=status_style,
                ),
            ],
            [InlineKeyboardButton(
                "🗑 حذف القناة",
                callback_data=f"delete_channel_{channel_db_id}",
                style="danger",
            )],
            [InlineKeyboardButton(
                get_text(lang, "btn_back"),
                callback_data="channel_management",
                style="primary",
            )],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if re.fullmatch(r"set_channel_message_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if not channel_info:
            return
        context.user_data["waiting_for"] = "channel_message"
        context.user_data["channel_username"] = channel_info["channel_username"]
        context.user_data["channel_manage_return_id"] = channel_db_id
        await query.edit_message_text(
            f"📝 أرسل رسالة الاشتراك الإجباري الخاصة بقناة @{channel_info['channel_username']}:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data=f"manage_channel_{channel_db_id}",
                    style="primary",
                )
            ]]),
        )
        return

    if re.fullmatch(r"delete_channel_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if channel_info and delete_channel(channel_info["channel_username"]):
            await query.edit_message_text(
                f"✅ تم حذف القناة @{channel_info['channel_username']} بنجاح.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
        else:
            await query.edit_message_text(
                "❌ تعذر حذف القناة.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data="channel_management",
                        style="primary",
                    )
                ]]),
            )
        return

    if re.fullmatch(r"toggle_subscription_\d+", data):
        if not is_admin(user_id):
            return
        channel_db_id = int(data.rsplit("_", 1)[1])
        channel_info = get_channel_by_id(channel_db_id)
        if not channel_info:
            return
        new_status = toggle_subscription(channel_info["channel_username"])
        action = "تفعيل" if new_status else "تعطيل"
        await query.edit_message_text(
            f"✅ تم {action} الاشتراك الإجباري لقناة @{channel_info['channel_username']}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data=f"manage_channel_{channel_db_id}",
                    style="primary",
                )
            ]]),
        )
        return

    # توافق مع أزرار رسائل الإدارة القديمة: تستهدف آخر قناة مضافة.
    if data == "set_channel_message":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if not channel_info:
            await query.edit_message_text(
                "❌ لا توجد قناة محددة",
                reply_markup=get_channel_management_keyboard(lang),
            )
            return
        context.user_data["waiting_for"] = "channel_message"
        context.user_data["channel_username"] = channel_info["channel_username"]
        context.user_data["channel_manage_return_id"] = channel_info["id"]
        await query.edit_message_text(
            "📝 أرسل رسالة الاشتراك الإجباري:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data=f"manage_channel_{channel_info['id']}",
                    style="primary",
                )
            ]]),
        )
        return

    if data == "delete_channel":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            delete_channel(channel_info["channel_username"])
            await query.edit_message_text(
                "✅ تم حذف القناة بنجاح",
                reply_markup=get_channel_management_keyboard(lang),
            )
        else:
            await query.edit_message_text(
                "❌ لا توجد قناة",
                reply_markup=get_channel_management_keyboard(lang),
            )
        return

    if data == "toggle_subscription":
        if not is_admin(user_id):
            return
        channel_info = get_channel_info(only_enabled=False)
        if channel_info:
            new_status = toggle_subscription(channel_info["channel_username"])
            action = "تفعيل" if new_status else "تعطيل"
            await query.edit_message_text(
                f"✅ تم {action} الاشتراك الإجباري",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_text(lang, "btn_back"),
                        callback_data=f"manage_channel_{channel_info['id']}",
                        style="primary",
                    )
                ]]),
            )
        return

    # أقسام الأدمن القديمة الأساسية (موجودة ومفعلة)
''',
)

# بعد حفظ رسالة قناة، يرجع لنفس القناة التي كان يديرها.
replace_once(
    r'''    if waiting_for == "channel_message" and is_admin(user_id):
        msg = update.message.text or ""
        ch = context.user_data.get("channel_username")
        ok = bool(ch) and set_channel_message(ch, msg)
        context.user_data["waiting_for"] = None
        context.user_data["channel_username"] = None
        await update.message.reply_text("✅ تم حفظ الرسالة" if ok else "❌ فشل حفظ الرسالة",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return
''',
    r'''    if waiting_for == "channel_message" and is_admin(user_id):
        msg = update.message.text or ""
        ch = context.user_data.get("channel_username")
        return_id = context.user_data.get("channel_manage_return_id")
        ok = bool(ch) and set_channel_message(ch, msg)
        context.user_data["waiting_for"] = None
        context.user_data["channel_username"] = None
        context.user_data.pop("channel_manage_return_id", None)
        back_callback = f"manage_channel_{return_id}" if return_id else "channel_management"
        await update.message.reply_text(
            "✅ تم حفظ الرسالة" if ok else "❌ فشل حفظ الرسالة",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data=back_callback,
                    style="primary",
                )
            ]]),
        )
        return
''',
)

# عند إضافة قناة، النص يوضح أنها أضيفت للقائمة ولا يستبدل القنوات السابقة.
replace_once(
    r'''    if waiting_for == "channel_username" and is_admin(user_id):
        channel_username = (update.message.text or "").strip().replace("@", "")
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            ok = set_channel(channel_username, chat.id, chat.title)
            text = f"✅ تم تعيين القناة @{channel_username}\n🆔 {chat.id}\n📢 {chat.title}" if ok else "❌ فشل تعيين القناة"
        except Exception as e:
            text = f"❌ خطأ: {str(e)[:200]}"
        context.user_data["waiting_for"] = None
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "btn_back"), callback_data="channel_management")]]))
        return
''',
    r'''    if waiting_for == "channel_username" and is_admin(user_id):
        channel_username = (update.message.text or "").strip().replace("@", "")
        try:
            chat = await context.bot.get_chat(f"@{channel_username}")
            ok = set_channel(channel_username, chat.id, chat.title)
            text = (
                f"✅ تمت إضافة/تحديث القناة @{channel_username}\n"
                f"🆔 {chat.id}\n"
                f"📢 {chat.title}\n\n"
                "القنوات السابقة بقيت كما هي."
                if ok else "❌ فشل إضافة القناة"
            )
        except Exception as e:
            text = f"❌ خطأ: {str(e)[:200]}"
        context.user_data["waiting_for"] = None
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(lang, "btn_back"),
                    callback_data="channel_management",
                    style="primary",
                )
            ]]),
        )
        return
''',
)

required_markers = (
    "def get_channels(only_enabled=True):",
    "def get_channel_by_id(channel_db_id: int):",
    "async def get_missing_subscription_channels",
    'callback_data=f"manage_channel_{channel[\'id\']}"',
    'callback_data=f"set_channel_message_{channel_db_id}"',
    'callback_data=f"toggle_subscription_{channel_db_id}"',
    'callback_data=f"delete_channel_{channel_db_id}"',
    "يجب عليك الاشتراك في القنوات التالية",
)
for marker in required_markers:
    if marker not in source:
        raise RuntimeError(f"Missing required marker: {marker}")

# صندوق الوارد يبقى يدوياً كما هو.
if "application.job_queue" in source or "run_repeating" in source:
    raise RuntimeError("Unexpected background inbox polling was introduced")

ast.parse(source)
path.write_text(source, encoding="utf-8")

# مزامنة نسخة التصدير مع ملف التشغيل فقط.
export_path = Path("bot_code_export.txt")
export_text = export_path.read_text(encoding="utf-8")
marker = "#!/usr/bin/env python3"
marker_index = export_text.find(marker)
if marker_index == -1:
    raise RuntimeError("telegram_bot.py marker not found in bot_code_export.txt")
export_path.write_text(export_text[:marker_index] + source, encoding="utf-8")
