from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import Database


# ============================================================
# ISI TOKEN BOT DI SINI
# ============================================================
BOT_TOKEN = "8842731707:AAEE_OHzL-IN5NpsjXkOjDCF2aOtfBmF2OA"

# Grup dan Topic RESULT TOGEL milik Anda.
GROUP_CHAT_ID = -1004405531074
MESSAGE_THREAD_ID = 35

# Database PostgreSQL Railway tetap dibaca dari Variables Railway.
DATABASE_URL = os.getenv("DATABASE_URL", "")

TIMEZONE = ZoneInfo("Asia/Jakarta")
REMINDER_INTERVAL_MINUTES = 5
CHECK_INTERVAL_SECONDS = 30

# Kosongkan agar semua admin grup boleh memakai fitur admin.
# Atau isi ID Telegram owner, misalnya: {123456789}
OWNER_USER_IDS: set[int] = set()

MARKETS_FILE = Path("markets.json")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("result-bot")

db = Database(DATABASE_URL)

# Lock sederhana agar dua staf tidak mengisi pasaran yang sama bersamaan.
# Lock hanya untuk proses input sesaat; data result tetap aman di PostgreSQL.
market_locks: dict[str, dict] = {}


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def actor_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def is_target_topic(update: Update) -> bool:
    msg = update.effective_message
    if not msg:
        return False
    return (
        update.effective_chat
        and update.effective_chat.id == GROUP_CHAT_ID
        and msg.message_thread_id == MESSAGE_THREAD_ID
    )


async def is_group_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if OWNER_USER_IDS:
        return user_id in OWNER_USER_IDS
    try:
        member = await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    allowed = await is_group_admin(context, user.id)
    if not allowed:
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
    return allowed


def main_keyboard(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✍️ ISI RESULT",
            callback_data=f"fill:{slug}",
        )
    ]])


def check_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📢 RESULT HARI INI",
            callback_data="check:DONE",
        )],
        [InlineKeyboardButton(
            "🔴 BELUM DIISI",
            callback_data="check:PENDING",
        )],
        [InlineKeyboardButton(
            "🕒 BELUM WAKTUNYA",
            callback_data="check:WAITING",
        )],
        [InlineKeyboardButton(
            "⚫ OFF HARI INI",
            callback_data="check:OFF",
        )],
    ])


def market_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ TAMBAH PASARAN",
            callback_data="market:add",
        )],
        [InlineKeyboardButton(
            "✏️ EDIT PASARAN",
            callback_data="market:edit:0",
        )],
        [InlineKeyboardButton(
            "⛔ NONAKTIFKAN",
            callback_data="market:disable:0",
        )],
        [InlineKeyboardButton(
            "✅ AKTIFKAN KEMBALI",
            callback_data="market:enable:0",
        )],
        [InlineKeyboardButton(
            "📋 DAFTAR PASARAN",
            callback_data="market:list:0",
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


def final_text(
    row,
    value: str,
    admin_name: str,
    filled_at: datetime,
) -> str:
    shown = "OFF HARI INI" if value == "OFF" else value
    icon = "⚫" if value == "OFF" else "🟢"
    return (
        "📢 <b>RESULT PASARAN</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        f"{icon} RESULT : <b>{html.escape(shown)}</b>\n\n"
        f"👤 {html.escape(admin_name)} "
        f"🕒 {filled_at.astimezone(TIMEZONE).strftime('%H:%M')}"
    )


def input_prompt_text(row) -> str:
    return (
        "✍️ <b>MASUKKAN RESULT</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n\n"
        "Jika pasaran libur cukup ketik: <b>OFF</b>"
    )


def confirm_text(row, value: str, update_mode: bool = False) -> str:
    shown = "OFF HARI INI" if value == "OFF" else value
    icon = "⚫" if value == "OFF" else "🟢"
    title = "KONFIRMASI UPDATE" if update_mode else "KONFIRMASI"
    return (
        f"📋 <b>{title}</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        f"{icon} RESULT : <b>{html.escape(shown)}</b>"
    )


def scheduled_at(target_date: date, open_time: time) -> datetime:
    return datetime.combine(target_date, open_time, tzinfo=TIMEZONE)


async def send_topic_message(context, text: str, **kwargs):
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
    except (BadRequest, Forbidden) as exc:
        log.warning("Tidak bisa edit pesan %s: %s", message_id, exc)
        return False


async def cleanup_user_messages(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    keep_state: bool = False,
) -> None:
    for key in (
        "prompt_message_id",
        "input_message_id",
        "confirm_message_id",
        "wizard_message_id",
    ):
        await safe_delete(context.bot, context.user_data.get(key))

    if keep_state:
        for key in (
            "prompt_message_id",
            "input_message_id",
            "confirm_message_id",
            "wizard_message_id",
        ):
            context.user_data.pop(key, None)
    else:
        context.user_data.clear()


def release_lock(slug: str, user_id: int | None = None) -> None:
    lock = market_locks.get(slug)
    if not lock:
        return
    if user_id is None or lock["user_id"] == user_id:
        market_locks.pop(slug, None)


def claim_lock(slug: str, user) -> tuple[bool, str | None]:
    current = now_local()
    existing = market_locks.get(slug)
    if existing:
        started = existing["started_at"]
        if current - started > timedelta(minutes=2):
            market_locks.pop(slug, None)
        elif existing["user_id"] != user.id:
            return False, existing["name"]

    market_locks[slug] = {
        "user_id": user.id,
        "name": actor_name(user),
        "started_at": current,
    }
    return True, None


async def schedule_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    current = now_local()
    today = current.date()

    await db.ensure_day(today)
    rows = await db.get_today_rows(today)

    for row in rows:
        if row["status"] in ("DONE", "OFF_MANUAL", "OFF_SCHEDULE"):
            continue

        due = scheduled_at(today, row["open_time"])
        if current < due:
            continue

        if not row["main_message_id"]:
            msg = await send_topic_message(
                context,
                alarm_text(row),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard(row["slug"]),
            )
            if row["status"] == "WAITING":
                await db.mark_pending_and_main(row["id"], msg.message_id)
            else:
                await db.set_main_message(row["id"], msg.message_id)
            row = await db.get_row_by_slug(today, row["slug"])

        elif row["status"] == "WAITING":
            await db.mark_pending_and_main(
                row["id"],
                row["main_message_id"],
            )
            row = await db.get_row_by_slug(today, row["slug"])

        late_minutes = max(
            0,
            int((current - due).total_seconds() // 60),
        )
        if late_minutes < REMINDER_INTERVAL_MINUTES:
            continue

        last = row["last_reminder_at"]
        if last:
            last_local = last.astimezone(TIMEZONE)
            if current - last_local < timedelta(
                minutes=REMINDER_INTERVAL_MINUTES
            ):
                continue

        # Hapus reminder lama agar topic tidak menumpuk.
        await safe_delete(
            context.bot,
            row["reminder_message_id"],
        )

        # Kirim reminder baru agar Telegram memberi notifikasi baru.
        reminder = await send_topic_message(
            context,
            reminder_text(row, late_minutes),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(row["slug"]),
        )
        await db.set_reminder(
            row["id"],
            reminder.message_id,
            current,
        )


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    await update.effective_message.reply_text(
        "🤖 Bot Result aktif.\n\n"
        "Gunakan /cek untuk melihat status hari ini.",
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def cmd_cek(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    await db.ensure_day(now_local().date())
    await update.effective_message.reply_text(
        "📋 <b>CEK STATUS PASARAN</b>\n\nPilih status:",
        parse_mode=ParseMode.HTML,
        reply_markup=check_keyboard(),
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def check_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    q = update.callback_query
    await q.answer()

    today = now_local().date()
    await db.ensure_day(today)
    rows = await db.get_today_rows(today)
    category = q.data.split(":", 1)[1]

    if category == "DONE":
        selected = [r for r in rows if r["status"] == "DONE"]
        title = "📢 RESULT HARI INI"
    elif category == "PENDING":
        selected = [r for r in rows if r["status"] == "PENDING"]
        title = "🔴 BELUM DIISI"
    elif category == "WAITING":
        selected = [r for r in rows if r["status"] == "WAITING"]
        title = "🕒 BELUM WAKTUNYA"
    else:
        selected = [
            r for r in rows
            if r["status"] in ("OFF_MANUAL", "OFF_SCHEDULE")
        ]
        title = "⚫ OFF HARI INI"

    lines = [
        f"<b>{title}</b>",
        f"📅 {today.strftime('%d-%m-%Y')}",
        "",
    ]

    if not selected:
        lines.append("Tidak ada data.")
    else:
        for row in selected:
            if category == "DONE":
                lines.append(
                    f"🟢 {html.escape(row['name'])} — "
                    f"<b>{html.escape(row['result_value'] or '-')}</b>"
                )
            elif category == "PENDING":
                late = max(
                    0,
                    int((
                        now_local()
                        - scheduled_at(today, row["open_time"])
                    ).total_seconds() // 60),
                )
                lines.append(
                    f"🔴 {html.escape(row['name'])} "
                    f"({row['open_time'].strftime('%H:%M')}) "
                    f"— {late} menit"
                )
            elif category == "WAITING":
                lines.append(
                    f"🕒 {html.escape(row['name'])} "
                    f"({row['open_time'].strftime('%H:%M')})"
                )
            else:
                reason = (
                    "OFF HARI INI"
                    if row["status"] == "OFF_MANUAL"
                    else "LIBUR JADWAL"
                )
                lines.append(
                    f"⚫ {html.escape(row['name'])} — {reason}"
                )

    lines.extend(["", f"Total: <b>{len(selected)}</b>"])

    await q.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=check_keyboard(),
    )


async def fill_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    q = update.callback_query
    slug = q.data.split(":", 1)[1]
    row = await db.get_row_by_slug(now_local().date(), slug)

    if not row:
        await q.answer(
            "Data pasaran tidak ditemukan.",
            show_alert=True,
        )
        return

    if row["status"] == "DONE":
        await q.answer(
            f"Sudah diisi: {row['result_value']}",
            show_alert=True,
        )
        return

    if row["status"] in ("OFF_MANUAL", "OFF_SCHEDULE"):
        await q.answer(
            "Pasaran sudah OFF hari ini.",
            show_alert=True,
        )
        return

    user = update.effective_user
    claimed, holder = claim_lock(slug, user)
    if not claimed:
        await q.answer(
            f"Sedang diproses oleh {holder}.",
            show_alert=True,
        )
        return

    await q.answer()
    await cleanup_user_messages(context)

    context.user_data["state"] = "result_input"
    context.user_data["slug"] = slug
    context.user_data["update_mode"] = False

    prompt = await send_topic_message(
        context,
        input_prompt_text(row),
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Masukkan result atau OFF",
        ),
    )
    context.user_data["prompt_message_id"] = prompt.message_id


async def handle_result_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    if context.user_data.get("state") != "result_input":
        return
    if not await require_admin(update, context):
        return

    raw = update.effective_message.text.strip()
    value = "OFF" if raw.upper() == "OFF" else raw

    if value != "OFF" and (
        not value.isdigit()
        or len(value) < 1
        or len(value) > 10
    ):
        await update.effective_message.reply_text(
            "❌ Hasil harus berupa angka, atau ketik OFF.",
            message_thread_id=MESSAGE_THREAD_ID,
        )
        return

    slug = context.user_data["slug"]
    row = await db.get_row_by_slug(now_local().date(), slug)
    if not row:
        release_lock(slug, update.effective_user.id)
        await cleanup_user_messages(context)
        return

    context.user_data["proposed_result"] = value
    context.user_data["input_message_id"] = (
        update.effective_message.message_id
    )

    confirm = await update.effective_message.reply_text(
        confirm_text(
            row,
            value,
            context.user_data.get("update_mode", False),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ SIMPAN",
                callback_data=f"save:{slug}",
            ),
            InlineKeyboardButton(
                "❌ BATAL",
                callback_data=f"cancel:{slug}",
            ),
        ]]),
        message_thread_id=MESSAGE_THREAD_ID,
    )
    context.user_data["confirm_message_id"] = confirm.message_id
    context.user_data["state"] = "result_confirm"


async def save_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    q = update.callback_query
    slug = q.data.split(":", 1)[1]
    value = context.user_data.get("proposed_result")
    update_mode = bool(context.user_data.get("update_mode"))

    if (
        context.user_data.get("slug") != slug
        or not value
    ):
        await q.answer(
            "Sesi sudah berakhir. Mulai ulang.",
            show_alert=True,
        )
        return

    await q.answer()
    today = now_local().date()
    row = await db.get_row_by_slug(today, slug)
    if not row:
        release_lock(slug, update.effective_user.id)
        await cleanup_user_messages(context)
        return

    user = update.effective_user
    saved_at = now_local()
    result = await db.save_result(
        today,
        slug,
        value,
        user.id,
        actor_name(user),
        force_update=update_mode,
    )

    if result and result.get("error") == "already_done":
        await q.answer(
            "Result sudah pernah diisi. Gunakan /update.",
            show_alert=True,
        )
        return

    await safe_delete(
        context.bot,
        row["reminder_message_id"],
    )
    await db.clear_reminder(row["id"])

    edited = await safe_edit(
        context.bot,
        row["main_message_id"],
        final_text(
            row,
            value,
            actor_name(user),
            saved_at,
        ),
        None,
    )

    if not edited:
        msg = await send_topic_message(
            context,
            final_text(
                row,
                value,
                actor_name(user),
                saved_at,
            ),
            parse_mode=ParseMode.HTML,
        )
        await db.set_main_message(row["id"], msg.message_id)

    release_lock(slug, user.id)
    await cleanup_user_messages(context)


async def cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    q = update.callback_query
    slug = q.data.split(":", 1)[1]
    await q.answer("Dibatalkan.")
    release_lock(slug, update.effective_user.id)
    await cleanup_user_messages(context)


def paginate_buttons(
    rows,
    *,
    prefix: str,
    page: int,
    page_size: int = 8,
    show_value: bool = False,
) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    current = rows[start:start + page_size]

    buttons = []
    for row in current:
        label = row["name"]
        if show_value and row.get("result_value") is not None:
            label += f" — {row['result_value']}"
        buttons.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=f"{prefix}:{row['slug']}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "◀️",
            callback_data=f"{prefix}page:{page - 1}",
        ))
    nav.append(InlineKeyboardButton(
        f"{page + 1}/{total_pages}",
        callback_data="noop",
    ))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(
            "▶️",
            callback_data=f"{prefix}page:{page + 1}",
        ))
    buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


async def cmd_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    if not await require_admin(update, context):
        return

    await db.ensure_day(now_local().date())
    rows = [
        dict(r)
        for r in await db.get_today_rows(now_local().date())
        if r["status"] in ("DONE", "OFF_MANUAL")
    ]

    if not rows:
        await update.effective_message.reply_text(
            "Belum ada pasaran yang sudah diisi hari ini.",
            message_thread_id=MESSAGE_THREAD_ID,
        )
        return

    context.chat_data["update_rows"] = rows
    await update.effective_message.reply_text(
        "✏️ <b>PILIH PASARAN YANG MAU DIUPDATE</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=paginate_buttons(
            rows,
            prefix="upd",
            page=0,
            show_value=True,
        ),
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def update_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":", 1)[1])
    rows = context.chat_data.get("update_rows", [])
    await q.edit_message_reply_markup(
        reply_markup=paginate_buttons(
            rows,
            prefix="upd",
            page=page,
            show_value=True,
        )
    )


async def update_pick_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    q = update.callback_query
    slug = q.data.split(":", 1)[1]
    row = await db.get_row_by_slug(now_local().date(), slug)
    if not row:
        await q.answer("Data tidak ditemukan.", show_alert=True)
        return

    user = update.effective_user
    claimed, holder = claim_lock(slug, user)
    if not claimed:
        await q.answer(
            f"Sedang diproses oleh {holder}.",
            show_alert=True,
        )
        return

    await q.answer()
    await cleanup_user_messages(context)

    context.user_data["state"] = "result_input"
    context.user_data["slug"] = slug
    context.user_data["update_mode"] = True

    prompt = await send_topic_message(
        context,
        (
            "✏️ <b>UPDATE RESULT</b>\n\n"
            f"📍 <b>{html.escape(row['name'])}</b> "
            f"({row['open_time'].strftime('%H:%M')})\n"
            f"Hasil sekarang : "
            f"<b>{html.escape(row['result_value'] or '-')}</b>\n\n"
            "Kirim hasil baru. Jika OFF, cukup ketik: <b>OFF</b>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Masukkan hasil baru",
        ),
    )
    context.user_data["prompt_message_id"] = prompt.message_id


async def cmd_pasaran(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    if not await require_admin(update, context):
        return

    await update.effective_message.reply_text(
        "⚙️ <b>KELOLA PASARAN</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=market_admin_keyboard(),
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def market_add_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return
    q = update.callback_query
    await q.answer()

    await cleanup_user_messages(context)
    context.user_data["state"] = "market_add_name"
    msg = await send_topic_message(
        context,
        "➕ <b>TAMBAH PASARAN</b>\n\nKirim nama pasaran.",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Nama pasaran",
        ),
    )
    context.user_data["wizard_message_id"] = msg.message_id


async def handle_market_wizard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return

    state = context.user_data.get("state", "")
    if not state.startswith("market_"):
        return
    if not await require_admin(update, context):
        return

    text = update.effective_message.text.strip()
    context.user_data["input_message_id"] = (
        update.effective_message.message_id
    )

    if state == "market_add_name":
        if len(text) < 2:
            await update.effective_message.reply_text(
                "Nama terlalu pendek.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return

        context.user_data["market_name"] = text.upper()
        context.user_data["market_slug"] = slugify(text)
        context.user_data["state"] = "market_add_time"

        await safe_delete(
            context.bot,
            context.user_data.get("wizard_message_id"),
        )
        msg = await send_topic_message(
            context,
            "⏰ Kirim jam buka dalam format <b>HH:MM</b>.\n"
            "Contoh: <b>17:45</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder="HH:MM",
            ),
        )
        context.user_data["wizard_message_id"] = msg.message_id
        return

    if state == "market_add_time":
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
            await update.effective_message.reply_text(
                "❌ Format jam harus HH:MM, contoh 17:45.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return

        context.user_data["market_time"] = text
        context.user_data["state"] = "market_add_days"

        await safe_delete(
            context.bot,
            context.user_data.get("wizard_message_id"),
        )
        msg = await send_topic_message(
            context,
            (
                "📅 Kirim hari aktif memakai angka dipisahkan koma.\n\n"
                "0=Senin, 1=Selasa, 2=Rabu, 3=Kamis,\n"
                "4=Jumat, 5=Sabtu, 6=Minggu\n\n"
                "Contoh setiap hari: <b>0,1,2,3,4,5,6</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder="0,1,2,3,4,5,6",
            ),
        )
        context.user_data["wizard_message_id"] = msg.message_id
        return

    if state == "market_add_days":
        try:
            days = sorted(set(int(x.strip()) for x in text.split(",")))
        except ValueError:
            days = []
        if not days or any(day < 0 or day > 6 for day in days):
            await update.effective_message.reply_text(
                "❌ Hari aktif tidak valid.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return

        try:
            market = await db.add_market(
                slug=context.user_data["market_slug"],
                name=context.user_data["market_name"],
                open_time=context.user_data["market_time"],
                active_days=days,
                result_digits=4,
            )
            await db.ensure_day(now_local().date())
            await send_topic_message(
                context,
                (
                    "✅ <b>PASARAN BERHASIL DITAMBAHKAN</b>\n\n"
                    f"📍 {html.escape(market['name'])}\n"
                    f"⏰ {market['open_time'].strftime('%H:%M')}\n"
                    f"📅 Hari aktif: {','.join(map(str, days))}"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            await send_topic_message(
                context,
                f"❌ Gagal menambah pasaran: {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )
        await cleanup_user_messages(context)


async def market_list_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    q = update.callback_query
    await q.answer()
    page = int(q.data.rsplit(":", 1)[1])
    rows = [dict(r) for r in await db.list_markets(True)]
    page_size = 12
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    current = rows[page * page_size:(page + 1) * page_size]

    lines = ["📋 <b>DAFTAR PASARAN</b>", ""]
    for row in current:
        status = "🟢" if row["active"] else "⚫"
        days = row["active_days"]
        if isinstance(days, str):
            days = json.loads(days)
        lines.append(
            f"{status} {html.escape(row['name'])} "
            f"({row['open_time'].strftime('%H:%M')}) "
            f"[{','.join(map(str, days))}]"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "◀️",
            callback_data=f"market:list:{page - 1}",
        ))
    nav.append(InlineKeyboardButton(
        f"{page + 1}/{total_pages}",
        callback_data="noop",
    ))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(
            "▶️",
            callback_data=f"market:list:{page + 1}",
        ))

    await q.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            nav,
            [InlineKeyboardButton(
                "⬅️ KEMBALI",
                callback_data="market:home",
            )],
        ]),
    )


async def market_choose_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    page = int(parts[2])
    await q.answer()

    include_inactive = action == "enable"
    rows = [
        dict(r)
        for r in await db.list_markets(include_inactive=True)
        if (
            (action == "enable" and not r["active"])
            or (action in ("edit", "disable") and r["active"])
        )
    ]

    context.chat_data[f"market_{action}_rows"] = rows
    if not rows:
        await q.edit_message_text(
            "Tidak ada pasaran pada kategori ini.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ KEMBALI",
                    callback_data="market:home",
                )
            ]]),
        )
        return

    prefix = {
        "edit": "medit",
        "disable": "mdisable",
        "enable": "menable",
    }[action]
    await q.edit_message_text(
        {
            "edit": "✏️ Pilih pasaran yang mau diedit.",
            "disable": "⛔ Pilih pasaran yang mau dinonaktifkan.",
            "enable": "✅ Pilih pasaran yang mau diaktifkan kembali.",
        }[action],
        reply_markup=paginate_buttons(
            rows,
            prefix=prefix,
            page=page,
        ),
    )


async def market_toggle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return
    q = update.callback_query
    action, slug = q.data.split(":", 1)
    active = action == "menable"
    market = await db.set_market_active(slug, active)
    if not market:
        await q.answer("Pasaran tidak ditemukan.", show_alert=True)
        return
    await q.answer("Berhasil.")
    await q.edit_message_text(
        (
            f"{'✅ DIAKTIFKAN' if active else '⛔ DINONAKTIFKAN'}\n\n"
            f"📍 {html.escape(market['name'])}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⬅️ KEMBALI",
                callback_data="market:home",
            )
        ]]),
    )


async def market_edit_pick_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return
    q = update.callback_query
    slug = q.data.split(":", 1)[1]
    market = await db.get_market(slug)
    if not market:
        await q.answer("Pasaran tidak ditemukan.", show_alert=True)
        return
    await q.answer()
    days = market["active_days"]
    if isinstance(days, str):
        days = json.loads(days)
    await q.edit_message_text(
        (
            "✏️ <b>EDIT PASARAN</b>\n\n"
            f"📍 {html.escape(market['name'])}\n"
            f"⏰ {market['open_time'].strftime('%H:%M')}\n"
            f"📅 {','.join(map(str, days))}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📝 UBAH NAMA",
                callback_data=f"mfield:name:{slug}",
            )],
            [InlineKeyboardButton(
                "⏰ UBAH JAM",
                callback_data=f"mfield:time:{slug}",
            )],
            [InlineKeyboardButton(
                "📅 UBAH HARI AKTIF",
                callback_data=f"mfield:days:{slug}",
            )],
            [InlineKeyboardButton(
                "⬅️ KEMBALI",
                callback_data="market:home",
            )],
        ]),
    )


async def market_field_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return
    q = update.callback_query
    _, field, slug = q.data.split(":", 2)
    market = await db.get_market(slug)
    if not market:
        await q.answer("Pasaran tidak ditemukan.", show_alert=True)
        return
    await q.answer()

    await cleanup_user_messages(context)
    context.user_data["state"] = f"market_edit_{field}"
    context.user_data["market_slug"] = slug

    instructions = {
        "name": "Kirim nama baru pasaran.",
        "time": "Kirim jam baru format HH:MM. Contoh: 17:45",
        "days": (
            "Kirim hari aktif. 0=Senin sampai 6=Minggu.\n"
            "Contoh setiap hari: 0,1,2,3,4,5,6"
        ),
    }
    msg = await send_topic_message(
        context,
        f"✏️ <b>{html.escape(market['name'])}</b>\n\n"
        f"{instructions[field]}",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(selective=True),
    )
    context.user_data["wizard_message_id"] = msg.message_id


async def handle_market_edit_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    state = context.user_data.get("state", "")
    if not state.startswith("market_edit_"):
        return
    if not await require_admin(update, context):
        return

    field = state.removeprefix("market_edit_")
    slug = context.user_data["market_slug"]
    text = update.effective_message.text.strip()
    context.user_data["input_message_id"] = (
        update.effective_message.message_id
    )

    kwargs = {}
    if field == "name":
        if len(text) < 2:
            return
        kwargs["name"] = text.upper()
    elif field == "time":
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
            await update.effective_message.reply_text(
                "❌ Format jam harus HH:MM.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return
        kwargs["open_time"] = text
    elif field == "days":
        try:
            days = sorted(set(int(x.strip()) for x in text.split(",")))
        except ValueError:
            days = []
        if not days or any(day < 0 or day > 6 for day in days):
            await update.effective_message.reply_text(
                "❌ Hari aktif tidak valid.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return
        kwargs["active_days"] = days

    market = await db.update_market(slug, **kwargs)
    await send_topic_message(
        context,
        (
            "✅ <b>PASARAN BERHASIL DIUPDATE</b>\n\n"
            f"📍 {html.escape(market['name'])}\n"
            f"⏰ {market['open_time'].strftime('%H:%M')}"
        ),
        parse_mode=ParseMode.HTML,
    )
    await cleanup_user_messages(context)


async def market_home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "⚙️ <b>KELOLA PASARAN</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=market_admin_keyboard(),
    )


async def noop_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()


async def cmd_reload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return
    if not await require_admin(update, context):
        return

    items = json.loads(MARKETS_FILE.read_text(encoding="utf-8"))
    await db.sync_markets(items)
    await db.ensure_day(now_local().date())
    await update.effective_message.reply_text(
        f"✅ {len(items)} pasaran dari markets.json dimuat ulang.",
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def post_init(app: Application) -> None:
    if BOT_TOKEN == "ISI_BOT_TOKEN_DI_SINI":
        raise RuntimeError(
            "Isi BOT_TOKEN di bagian atas file bot.py terlebih dahulu."
        )
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL belum tersedia. Tambahkan PostgreSQL di Railway."
        )

    await db.connect()

    if MARKETS_FILE.exists():
        items = json.loads(MARKETS_FILE.read_text(encoding="utf-8"))
        await db.sync_markets(items)

    await db.ensure_day(now_local().date())

    app.job_queue.run_repeating(
        schedule_tick,
        interval=CHECK_INTERVAL_SECONDS,
        first=3,
        name="schedule-check",
    )
    log.info(
        "Bot aktif di chat %s, topic %s.",
        GROUP_CHAT_ID,
        MESSAGE_THREAD_ID,
    )


async def post_shutdown(app: Application) -> None:
    await db.close()


def build_app() -> Application:
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("pasaran", cmd_pasaran))
    app.add_handler(CommandHandler("reload", cmd_reload))

    app.add_handler(CallbackQueryHandler(
        check_callback,
        pattern=r"^check:",
    ))
    app.add_handler(CallbackQueryHandler(
        fill_callback,
        pattern=r"^fill:",
    ))
    app.add_handler(CallbackQueryHandler(
        save_callback,
        pattern=r"^save:",
    ))
    app.add_handler(CallbackQueryHandler(
        cancel_callback,
        pattern=r"^cancel:",
    ))

    app.add_handler(CallbackQueryHandler(
        update_page_callback,
        pattern=r"^updpage:",
    ))
    app.add_handler(CallbackQueryHandler(
        update_pick_callback,
        pattern=r"^upd:",
    ))

    app.add_handler(CallbackQueryHandler(
        market_add_start,
        pattern=r"^market:add$",
    ))
    app.add_handler(CallbackQueryHandler(
        market_list_callback,
        pattern=r"^market:list:",
    ))
    app.add_handler(CallbackQueryHandler(
        market_choose_callback,
        pattern=r"^market:(edit|disable|enable):",
    ))
    app.add_handler(CallbackQueryHandler(
        market_toggle_callback,
        pattern=r"^(mdisable|menable):",
    ))
    app.add_handler(CallbackQueryHandler(
        market_edit_pick_callback,
        pattern=r"^medit:",
    ))
    app.add_handler(CallbackQueryHandler(
        market_field_callback,
        pattern=r"^mfield:",
    ))
    app.add_handler(CallbackQueryHandler(
        market_home_callback,
        pattern=r"^market:home$",
    ))
    app.add_handler(CallbackQueryHandler(
        noop_callback,
        pattern=r"^noop$",
    ))

    # Urutan handler penting:
    # wizard kelola pasaran lebih dahulu, baru input result.
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_market_edit_input,
    ), group=0)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_market_wizard,
    ), group=1)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_result_input,
    ), group=2)

    return app


if __name__ == "__main__":
    build_app().run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
