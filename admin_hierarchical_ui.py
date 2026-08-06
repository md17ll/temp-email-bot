#!/usr/bin/env python3
"""تنظيم لوحة الأدمن بشكل هرمي فقط، مع إبقاء منطق البوت الأصلي كما هو."""

import telegram_bot as bot


_original_button_callback = bot.button_callback


def _single_button(text: str, callback_data: str, style: str = "primary"):
    """إنشاء زر واحد طويل في سطر مستقل."""
    return [bot.InlineKeyboardButton(text, callback_data=callback_data, style=style)]


def get_admin_panel_keyboard(_lang, user_id):
    """القائمة الرئيسية الهرمية للأدمن؛ كل قسم بزر طويل مستقل."""
    rows = [
        _single_button(
            "📊 الدخول إلى قسم الإحصائيات الشاملة للبوت",
            "section_stats",
        ),
        _single_button(
            "👥 الدخول إلى قسم إدارة الأعضاء والمشرفين والحظر",
            "admin_group_members",
        ),
        _single_button(
            "📧 الدخول إلى قسم إدارة الإيميلات والدومينات",
            "admin_group_emails",
        ),
        _single_button(
            "📢 الدخول إلى قسم القنوات والإذاعة وتوجيه الرسائل",
            "admin_group_communications",
        ),
        _single_button(
            "⚙️ الدخول إلى قسم إعدادات البوت ورسالة الترحيب",
            "admin_group_settings",
        ),
        _single_button(
            "🔙 الرجوع إلى القائمة الرئيسية للمستخدم",
            "back_to_menu",
        ),
    ]
    return bot.InlineKeyboardMarkup(rows)


def _members_group_keyboard(user_id: int):
    rows = [
        _single_button(
            "👥 فتح قسم إدارة الأعضاء والمستخدمين بالكامل",
            "section_members",
        ),
        _single_button(
            "🛑 فتح قسم حظر وفك حظر المستخدمين",
            "section_ban",
            "danger",
        ),
    ]
    if user_id == bot.ADMIN_ID:
        rows.append(
            _single_button(
                "👮 فتح قسم إدارة المشرفين والصلاحيات",
                "section_admins",
            )
        )
    rows.append(
        _single_button(
            "🔙 الرجوع إلى لوحة تحكم المشرف الرئيسية",
            "admin_panel",
        )
    )
    return bot.InlineKeyboardMarkup(rows)


def _emails_group_keyboard():
    rows = [
        _single_button(
            "🔢 فتح إعداد الحد الأقصى لإنشاء الإيميلات",
            "section_email_limit",
        ),
        _single_button(
            "🌐 فتح قسم إدارة الدومينات المدفوعة",
            "section_paid_domains",
        ),
        _single_button(
            "🔙 الرجوع إلى لوحة تحكم المشرف الرئيسية",
            "admin_panel",
        ),
    ]
    return bot.InlineKeyboardMarkup(rows)


def _communications_group_keyboard():
    rows = [
        _single_button(
            "📢 فتح قسم إدارة قنوات الاشتراك الإجباري",
            "channel_management",
        ),
        _single_button(
            "📣 فتح قسم إرسال الإذاعة إلى المستخدمين",
            "section_broadcast",
        ),
        _single_button(
            "📨 فتح قسم توجيه رسائل المستخدمين إلى الأدمن",
            "section_forward",
        ),
        _single_button(
            "🔙 الرجوع إلى لوحة تحكم المشرف الرئيسية",
            "admin_panel",
        ),
    ]
    return bot.InlineKeyboardMarkup(rows)


def _settings_group_keyboard():
    rows = [
        _single_button(
            "⚙️ فتح إعدادات تشغيل وإيقاف البوت",
            "section_settings",
        ),
        _single_button(
            "👋 فتح قسم إعداد وتعديل رسالة الترحيب",
            "section_welcome",
            "success",
        ),
        _single_button(
            "ℹ️ فتح صفحة معلومات وإصدار البوت",
            "bot_info",
        ),
        _single_button(
            "🔙 الرجوع إلى لوحة تحكم المشرف الرئيسية",
            "admin_panel",
        ),
    ]
    return bot.InlineKeyboardMarkup(rows)


async def button_callback(update, context):
    """معالجة أقسام الواجهة الجديدة فقط، وتمرير باقي الأزرار للكود الأصلي."""
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return await _original_button_callback(update, context)

    data = query.data or ""
    new_sections = {
        "admin_group_members",
        "admin_group_emails",
        "admin_group_communications",
        "admin_group_settings",
    }
    if data not in new_sections:
        return await _original_button_callback(update, context)

    try:
        await query.answer()
    except Exception:
        pass

    if not bot.is_admin(user.id):
        return

    if data == "admin_group_members":
        text = "👥 قسم إدارة الأعضاء والمشرفين والحظر\n\nاختر القسم المطلوب:"
        keyboard = _members_group_keyboard(user.id)
    elif data == "admin_group_emails":
        text = "📧 قسم إدارة الإيميلات والدومينات\n\nاختر القسم المطلوب:"
        keyboard = _emails_group_keyboard()
    elif data == "admin_group_communications":
        text = "📢 قسم القنوات والإذاعة وتوجيه الرسائل\n\nاختر القسم المطلوب:"
        keyboard = _communications_group_keyboard()
    else:
        text = "⚙️ قسم إعدادات البوت ورسالة الترحيب\n\nاختر القسم المطلوب:"
        keyboard = _settings_group_keyboard()

    await query.edit_message_text(text, reply_markup=keyboard)


# استبدال واجهة لوحة الأدمن ومعالج الأزرار فقط.
bot.get_admin_panel_keyboard = get_admin_panel_keyboard
bot.button_callback = button_callback


if __name__ == "__main__":
    bot.main()
