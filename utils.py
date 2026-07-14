from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden

from config import (
    GROUP_CHAT_ID,
    MESSAGE_THREAD_ID,
    OWNER_USER_IDS,
    SESSION_TIMEOUT_MINUTES,
    TIMEZONE,
)

_market_locks: dict[str, dict] = {}


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def actor_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def is_target_topic(update) -> bool:
    message = update.effective_message
    return bool(
        message
        and update.effective_chat
        and update.effective_chat.id == GROUP_CHAT_ID
        and message.message_thread_id == MESSAGE_THREAD_ID
    )


async def is_admin(context, user_id: int) -> bool:
    if OWNER_USER_IDS:
        return user_id in OWNER_USER_IDS

    try:
        member = await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def require_admin(update, context) -> bool:
    user = update.effective_user
    if user and await is_admin(context, user.id):
        return True

    if update.callback_query:
        await update.callback_query.answer(
            "Fitur ini hanya untuk admin grup.",
            show_alert=True,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            "❌ Fitur ini hanya untuk admin grup.",
            message_thread_id=MESSAGE_THREAD_ID,
        )
    return False


async def send_topic(context, text: str, **kwargs):
    return await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=MESSAGE_THREAD_ID,
        text=text,
        **kwargs,
    )


async def safe_delete(bot, message_id: int | None) -> None:
    if not message_id:
        return

    try:
        await bot.delete_message(
            chat_id=GROUP_CHAT_ID,
            message_id=message_id,
        )
    except (BadRequest, Forbidden):
        pass


async def safe_edit(
    bot,
    message_id: int | None,
    text: str,
    reply_markup=None,
) -> bool:
    if not message_id:
        return False

    try:
        await bot.edit_message_text(
            chat_id=GROUP_CHAT_ID,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        return True
    except (BadRequest, Forbidden):
        return False


def fill_keyboard(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✍️ ISI RESULT",
            callback_data=f"fill:{slug}",
        )
    ]])


def result_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📢 RESULT HARI INI",
            callback_data="hasil:DONE",
        )],
        [InlineKeyboardButton(
            "🔴 BELUM DIISI",
            callback_data="hasil:PENDING",
        )],
        [InlineKeyboardButton(
            "🕒 BELUM WAKTUNYA",
            callback_data="hasil:WAITING",
        )],
        [InlineKeyboardButton(
            "⚫ OFF HARI INI",
            callback_data="hasil:OFF",
        )],
    ])


def alarm_text(row) -> str:
    return (
        "⏰ <b>WAKTUNYA RESULT</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        "🟡 STATUS : <b>MENUNGGU RESULT</b>"
    )


def reminder_text(row, late_minutes: int) -> str:
    return (
        "🔔 <b>PENGINGAT RESULT</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        "🔴 STATUS : <b>BELUM DIISI</b>\n\n"
        f"⏱ Terlambat : <b>{late_minutes} Menit</b>"
    )


def final_text(row, value: str, admin: str, at: datetime) -> str:
    shown_value = "OFF HARI INI" if value == "OFF" else value
    icon = "⚫" if value == "OFF" else "🟢"

    return (
        "📢 <b>RESULT PASARAN</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        f"{icon} RESULT : <b>{html.escape(shown_value)}</b>\n\n"
        f"👤 {html.escape(admin)} "
        f"🕒 {at.astimezone(TIMEZONE).strftime('%H:%M')}"
    )


def claim_lock(slug: str, user) -> tuple[bool, str | None]:
    current = now_local()
    lock = _market_locks.get(slug)

    if lock:
        if current - lock["started_at"] > timedelta(
            minutes=SESSION_TIMEOUT_MINUTES
        ):
            _market_locks.pop(slug, None)
        elif lock["user_id"] != user.id:
            return False, lock["name"]

    _market_locks[slug] = {
        "user_id": user.id,
        "name": actor_name(user),
        "started_at": current,
    }
    return True, None


def release_lock(slug: str, user_id: int | None = None) -> None:
    lock = _market_locks.get(slug)
    if not lock:
        return

    if user_id is None or lock["user_id"] == user_id:
        _market_locks.pop(slug, None)


async def cleanup_user_flow(context) -> None:
    for key in (
        "prompt_message_id",
        "input_message_id",
        "confirm_message_id",
        "wizard_message_id",
    ):
        await safe_delete(context.bot, context.user_data.get(key))

    context.user_data.clear()
