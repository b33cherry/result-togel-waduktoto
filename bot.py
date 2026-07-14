from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

from telegram import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    BOT_TOKEN,
    CHECK_INTERVAL_SECONDS,
    DATABASE_URL,
    MESSAGE_THREAD_ID,
)
from database import Database
from scheduler import scheduler_tick
from utils import (
    actor_name,
    claim_lock,
    cleanup_user_flow,
    fill_keyboard,
    final_text,
    is_target_topic,
    now_local,
    release_lock,
    require_admin,
    result_menu_keyboard,
    safe_delete,
    safe_edit,
    send_topic,
    slugify,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("result-bot")


def paginate(
    rows: list[dict],
    *,
    item_prefix: str,
    page_prefix: str,
    page: int,
    show_value: bool = False,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    current_rows = rows[start:start + page_size]

    buttons: list[list[InlineKeyboardButton]] = []
    for row in current_rows:
        label = row["name"]
        if show_value:
            label += f" — {row.get('result_value') or '-'}"

        buttons.append([InlineKeyboardButton(
            label[:60],
            callback_data=f"{item_prefix}:{row['slug']}",
        )])

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "◀️",
            callback_data=f"{page_prefix}:{page - 1}",
        ))

    navigation.append(InlineKeyboardButton(
        f"{page + 1}/{total_pages}",
        callback_data="noop",
    ))

    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton(
            "▶️",
            callback_data=f"{page_prefix}:{page + 1}",
        ))

    buttons.append(navigation)
    return InlineKeyboardMarkup(buttons)


async def cmd_hasil(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return

    db = context.application.bot_data["db"]
    await db.ensure_day(now_local().date())

    await update.effective_message.reply_text(
        "📋 <b>CEK STATUS PASARAN</b>\n\nPilih status:",
        parse_mode=ParseMode.HTML,
        reply_markup=result_menu_keyboard(),
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def hasil_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    db = context.application.bot_data["db"]
    today = now_local().date()
    rows = await db.today_rows(today)
    category = query.data.split(":", 1)[1]

    if category == "DONE":
        selected = [row for row in rows if row["status"] == "DONE"]
        title = "📢 RESULT HARI INI"
    elif category == "PENDING":
        selected = [row for row in rows if row["status"] == "PENDING"]
        title = "🔴 BELUM DIISI"
    elif category == "WAITING":
        selected = [row for row in rows if row["status"] == "WAITING"]
        title = "🕒 BELUM WAKTUNYA"
    else:
        selected = [
            row for row in rows
            if row["status"] in ("OFF_MANUAL", "OFF_SCHEDULE")
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
                lines.append(
                    f"🔴 {html.escape(row['name'])} "
                    f"({row['open_time'].strftime('%H:%M')})"
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

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=result_menu_keyboard(),
    )


async def fill_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    slug = query.data.split(":", 1)[1]
    db = context.application.bot_data["db"]
    row = await db.daily_by_slug(now_local().date(), slug)

    if not row:
        await query.answer("Data tidak ditemukan.", show_alert=True)
        return

    if row["status"] in ("DONE", "OFF_MANUAL"):
        await query.answer(
            "Sudah diisi. Gunakan /update.",
            show_alert=True,
        )
        return

    if row["status"] == "OFF_SCHEDULE":
        await query.answer(
            "Pasaran libur sesuai jadwal.",
            show_alert=True,
        )
        return

    claimed, holder = claim_lock(slug, update.effective_user)
    if not claimed:
        await query.answer(
            f"Sedang diproses oleh {holder}.",
            show_alert=True,
        )
        return

    await query.answer()
    await cleanup_user_flow(context)

    context.user_data.update({
        "state": "result_input",
        "slug": slug,
        "update_mode": False,
    })

    prompt = await send_topic(
        context,
        "✍️ <b>MASUKKAN RESULT</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n\n"
        "Jika pasaran libur cukup ketik: <b>OFF</b>",
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

    raw_value = update.effective_message.text.strip()
    value = "OFF" if raw_value.upper() == "OFF" else raw_value

    if value != "OFF" and not value.isdigit():
        await update.effective_message.reply_text(
            "❌ Hasil harus berupa angka atau OFF.",
            message_thread_id=MESSAGE_THREAD_ID,
        )
        return

    slug = context.user_data["slug"]
    db = context.application.bot_data["db"]
    row = await db.daily_by_slug(now_local().date(), slug)

    if not row:
        release_lock(slug, update.effective_user.id)
        await cleanup_user_flow(context)
        return

    context.user_data["value"] = value
    context.user_data["input_message_id"] = (
        update.effective_message.message_id
    )
    context.user_data["state"] = "result_confirm"

    shown = "OFF HARI INI" if value == "OFF" else value
    icon = "⚫" if value == "OFF" else "🟢"
    title = (
        "KONFIRMASI UPDATE"
        if context.user_data.get("update_mode")
        else "KONFIRMASI"
    )

    confirmation = await update.effective_message.reply_text(
        f"📋 <b>{title}</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        f"{icon} RESULT : <b>{html.escape(shown)}</b>",
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
    context.user_data["confirm_message_id"] = confirmation.message_id


async def save_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    slug = query.data.split(":", 1)[1]

    if context.user_data.get("slug") != slug:
        await query.answer("Sesi sudah berakhir.", show_alert=True)
        return

    await query.answer()

    db = context.application.bot_data["db"]
    today = now_local().date()
    row = await db.daily_by_slug(today, slug)

    if not row:
        release_lock(slug, update.effective_user.id)
        await cleanup_user_flow(context)
        return

    value = context.user_data["value"]
    update_mode = bool(context.user_data.get("update_mode"))
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
        await query.answer(
            "Sudah diisi. Gunakan /update.",
            show_alert=True,
        )
        return

    await safe_delete(context.bot, row["reminder_message_id"])
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
        message = await send_topic(
            context,
            final_text(
                row,
                value,
                actor_name(user),
                saved_at,
            ),
            parse_mode=ParseMode.HTML,
        )
        await db.set_main_message(row["id"], message.message_id)

    release_lock(slug, user.id)
    await cleanup_user_flow(context)


async def cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    slug = query.data.split(":", 1)[1]
    await query.answer("Dibatalkan.")

    release_lock(slug, update.effective_user.id)
    await cleanup_user_flow(context)


async def cmd_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not is_target_topic(update):
        return

    if not await require_admin(update, context):
        return

    db = context.application.bot_data["db"]
    rows = [
        dict(row)
        for row in await db.today_rows(now_local().date())
        if row["status"] in ("DONE", "OFF_MANUAL")
    ]

    if not rows:
        await update.effective_message.reply_text(
            "Belum ada result yang dapat diupdate.",
            message_thread_id=MESSAGE_THREAD_ID,
        )
        return

    context.chat_data["update_rows"] = rows

    await update.effective_message.reply_text(
        "✏️ <b>PILIH PASARAN YANG MAU DIUPDATE</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=paginate(
            rows,
            item_prefix="updatepick",
            page_prefix="updatepage",
            page=0,
            show_value=True,
        ),
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def update_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    page = int(query.data.split(":", 1)[1])
    rows = context.chat_data.get("update_rows", [])

    await query.edit_message_reply_markup(
        reply_markup=paginate(
            rows,
            item_prefix="updatepick",
            page_prefix="updatepage",
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

    query = update.callback_query
    slug = query.data.split(":", 1)[1]
    db = context.application.bot_data["db"]
    row = await db.daily_by_slug(now_local().date(), slug)

    if not row:
        await query.answer("Data tidak ditemukan.", show_alert=True)
        return

    claimed, holder = claim_lock(slug, update.effective_user)
    if not claimed:
        await query.answer(
            f"Sedang diproses oleh {holder}.",
            show_alert=True,
        )
        return

    await query.answer()
    await cleanup_user_flow(context)

    context.user_data.update({
        "state": "result_input",
        "slug": slug,
        "update_mode": True,
    })

    prompt = await send_topic(
        context,
        "✏️ <b>UPDATE RESULT</b>\n\n"
        f"📍 <b>{html.escape(row['name'])}</b> "
        f"({row['open_time'].strftime('%H:%M')})\n"
        f"Hasil sekarang : "
        f"<b>{html.escape(row['result_value'] or '-')}</b>\n\n"
        "Kirim hasil baru. Jika OFF, cukup ketik: <b>OFF</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Masukkan hasil baru",
        ),
    )
    context.user_data["prompt_message_id"] = prompt.message_id


def market_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ TAMBAH PASARAN",
            callback_data="marketadd",
        )],
        [InlineKeyboardButton(
            "✏️ EDIT PASARAN",
            callback_data="marketcategory:edit:0",
        )],
        [InlineKeyboardButton(
            "⛔ NONAKTIFKAN",
            callback_data="marketcategory:disable:0",
        )],
        [InlineKeyboardButton(
            "✅ AKTIFKAN KEMBALI",
            callback_data="marketcategory:enable:0",
        )],
        [InlineKeyboardButton(
            "📋 DAFTAR PASARAN",
            callback_data="marketlist:0",
        )],
    ])


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
        reply_markup=market_home_keyboard(),
        message_thread_id=MESSAGE_THREAD_ID,
    )


async def market_home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⚙️ <b>KELOLA PASARAN</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=market_home_keyboard(),
    )


async def market_add_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    await query.answer()
    await cleanup_user_flow(context)

    context.user_data["state"] = "market_add_name"

    prompt = await send_topic(
        context,
        "➕ <b>TAMBAH PASARAN</b>\n\nKirim nama pasaran.",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Nama pasaran",
        ),
    )
    context.user_data["wizard_message_id"] = prompt.message_id


async def market_category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    _, action, page_text = query.data.split(":")
    page = int(page_text)
    await query.answer()

    db = context.application.bot_data["db"]
    all_rows = [dict(row) for row in await db.list_markets(True)]

    if action == "edit":
        rows = [row for row in all_rows if row["active"]]
        item_prefix = "marketeditpick"
        page_prefix = "marketeditpage"
        title = "✏️ PILIH PASARAN"
    elif action == "disable":
        rows = [row for row in all_rows if row["active"]]
        item_prefix = "marketdisable"
        page_prefix = "marketdisablepage"
        title = "⛔ PILIH PASARAN"
    else:
        rows = [row for row in all_rows if not row["active"]]
        item_prefix = "marketenable"
        page_prefix = "marketenablepage"
        title = "✅ PILIH PASARAN"

    context.chat_data[f"market_rows_{action}"] = rows

    if not rows:
        await query.edit_message_text(
            "Tidak ada data.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ KEMBALI",
                    callback_data="markethome",
                )
            ]]),
        )
        return

    markup = paginate(
        rows,
        item_prefix=item_prefix,
        page_prefix=page_prefix,
        page=page,
    )
    markup = InlineKeyboardMarkup([
        *[list(row) for row in markup.inline_keyboard],
        [InlineKeyboardButton(
            "⬅️ KEMBALI",
            callback_data="markethome",
        )],
    ])

    await query.edit_message_text(
        title,
        reply_markup=markup,
    )


async def market_category_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    prefix, page_text = query.data.split(":")
    page = int(page_text)
    await query.answer()

    if prefix == "marketeditpage":
        action = "edit"
        item_prefix = "marketeditpick"
        page_prefix = "marketeditpage"
    elif prefix == "marketdisablepage":
        action = "disable"
        item_prefix = "marketdisable"
        page_prefix = "marketdisablepage"
    else:
        action = "enable"
        item_prefix = "marketenable"
        page_prefix = "marketenablepage"

    rows = context.chat_data.get(f"market_rows_{action}", [])
    markup = paginate(
        rows,
        item_prefix=item_prefix,
        page_prefix=page_prefix,
        page=page,
    )
    markup = InlineKeyboardMarkup([
        *[list(row) for row in markup.inline_keyboard],
        [InlineKeyboardButton(
            "⬅️ KEMBALI",
            callback_data="markethome",
        )],
    ])

    await query.edit_message_reply_markup(reply_markup=markup)


async def market_toggle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    action, slug = query.data.split(":", 1)
    active = action == "marketenable"

    db = context.application.bot_data["db"]
    row = await db.set_market_active(slug, active)

    if not row:
        await query.answer("Pasaran tidak ditemukan.", show_alert=True)
        return

    await query.answer("Berhasil.")

    await query.edit_message_text(
        f"{'✅ DIAKTIFKAN' if active else '⛔ DINONAKTIFKAN'}\n\n"
        f"📍 {html.escape(row['name'])}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⬅️ KEMBALI",
                callback_data="markethome",
            )
        ]]),
    )


async def market_edit_pick_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    slug = query.data.split(":", 1)[1]
    db = context.application.bot_data["db"]
    row = await db.get_market(slug)

    if not row:
        await query.answer("Pasaran tidak ditemukan.", show_alert=True)
        return

    await query.answer()

    days = row["active_days"]
    if isinstance(days, str):
        days = json.loads(days)

    await query.edit_message_text(
        "✏️ <b>EDIT PASARAN</b>\n\n"
        f"📍 {html.escape(row['name'])}\n"
        f"⏰ {row['open_time'].strftime('%H:%M')}\n"
        f"📅 {','.join(map(str, days))}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📝 UBAH NAMA",
                callback_data=f"marketfield:name:{slug}",
            )],
            [InlineKeyboardButton(
                "⏰ UBAH JAM",
                callback_data=f"marketfield:time:{slug}",
            )],
            [InlineKeyboardButton(
                "📅 UBAH HARI",
                callback_data=f"marketfield:days:{slug}",
            )],
            [InlineKeyboardButton(
                "⬅️ KEMBALI",
                callback_data="markethome",
            )],
        ]),
    )


async def market_field_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_admin(update, context):
        return

    query = update.callback_query
    _, field, slug = query.data.split(":", 2)
    await query.answer()
    await cleanup_user_flow(context)

    context.user_data["state"] = f"market_edit_{field}"
    context.user_data["market_slug"] = slug

    instructions = {
        "name": "Kirim nama baru pasaran.",
        "time": "Kirim jam baru dalam format HH:MM.",
        "days": (
            "Kirim hari aktif.\n"
            "0=Senin sampai 6=Minggu.\n"
            "Contoh: 0,1,2,3,4,5,6"
        ),
    }

    prompt = await send_topic(
        context,
        instructions[field],
        reply_markup=ForceReply(selective=True),
    )
    context.user_data["wizard_message_id"] = prompt.message_id


async def market_list_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    page = int(query.data.split(":", 1)[1])
    await query.answer()

    db = context.application.bot_data["db"]
    rows = [dict(row) for row in await db.list_markets(True)]

    page_size = 12
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    current = rows[page * page_size:(page + 1) * page_size]

    lines = ["📋 <b>DAFTAR PASARAN</b>", ""]
    for row in current:
        icon = "🟢" if row["active"] else "⚫"
        lines.append(
            f"{icon} {html.escape(row['name'])} "
            f"({row['open_time'].strftime('%H:%M')})"
        )

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "◀️",
            callback_data=f"marketlist:{page - 1}",
        ))

    navigation.append(InlineKeyboardButton(
        f"{page + 1}/{total_pages}",
        callback_data="noop",
    ))

    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton(
            "▶️",
            callback_data=f"marketlist:{page + 1}",
        ))

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            navigation,
            [InlineKeyboardButton(
                "⬅️ KEMBALI",
                callback_data="markethome",
            )],
        ]),
    )


async def handle_market_input(
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
    db = context.application.bot_data["db"]

    if state == "market_add_name":
        if len(text) < 2:
            await update.effective_message.reply_text(
                "❌ Nama terlalu pendek.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return

        context.user_data["market_name"] = text.upper()
        context.user_data["market_slug"] = slugify(text)
        context.user_data["state"] = "market_add_time"

        prompt = await send_topic(
            context,
            "⏰ Kirim jam buka dalam format HH:MM.\nContoh: 17:45",
            reply_markup=ForceReply(selective=True),
        )
        context.user_data["wizard_message_id"] = prompt.message_id
        return

    if state == "market_add_time":
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
            await update.effective_message.reply_text(
                "❌ Format jam harus HH:MM.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return

        context.user_data["market_time"] = text
        context.user_data["state"] = "market_add_days"

        prompt = await send_topic(
            context,
            "📅 Kirim hari aktif.\n"
            "0=Senin sampai 6=Minggu.\n"
            "Contoh: 0,1,2,3,4,5,6",
            reply_markup=ForceReply(selective=True),
        )
        context.user_data["wizard_message_id"] = prompt.message_id
        return

    if state == "market_add_days":
        try:
            days = sorted({
                int(part.strip())
                for part in text.split(",")
            })
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
                context.user_data["market_slug"],
                context.user_data["market_name"],
                context.user_data["market_time"],
                days,
            )
        except Exception:
            await update.effective_message.reply_text(
                "❌ Nama/slug pasaran sudah ada.",
                message_thread_id=MESSAGE_THREAD_ID,
            )
            return

        await db.ensure_day(now_local().date())

        await send_topic(
            context,
            "✅ <b>PASARAN DITAMBAHKAN</b>\n\n"
            f"📍 {html.escape(market['name'])}\n"
            f"⏰ {market['open_time'].strftime('%H:%M')}",
            parse_mode=ParseMode.HTML,
        )
        await cleanup_user_flow(context)
        return

    if state.startswith("market_edit_"):
        field = state.removeprefix("market_edit_")
        slug = context.user_data["market_slug"]
        kwargs = {}

        if field == "name":
            if len(text) < 2:
                await update.effective_message.reply_text(
                    "❌ Nama terlalu pendek.",
                    message_thread_id=MESSAGE_THREAD_ID,
                )
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

        else:
            try:
                days = sorted({
                    int(part.strip())
                    for part in text.split(",")
                })
            except ValueError:
                days = []

            if not days or any(day < 0 or day > 6 for day in days):
                await update.effective_message.reply_text(
                    "❌ Hari aktif tidak valid.",
                    message_thread_id=MESSAGE_THREAD_ID,
                )
                return
            kwargs["days"] = days

        market = await db.update_market(slug, **kwargs)

        await send_topic(
            context,
            "✅ <b>PASARAN DIUPDATE</b>\n\n"
            f"📍 {html.escape(market['name'])}\n"
            f"⏰ {market['open_time'].strftime('%H:%M')}",
            parse_mode=ParseMode.HTML,
        )
        await cleanup_user_flow(context)


async def noop_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()


async def text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    state = context.user_data.get("state", "")

    if state.startswith("market_"):
        await handle_market_input(update, context)
        return

    await handle_result_input(update, context)


async def post_init(application: Application) -> None:
    if BOT_TOKEN == "ISI_BOT_TOKEN_DI_SINI":
        raise RuntimeError("Isi BOT_TOKEN di config.py.")

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL belum tersedia. "
            "Tambahkan PostgreSQL dan variable DATABASE_URL di Railway."
        )

    db = Database(DATABASE_URL)
    await db.connect()
    application.bot_data["db"] = db

    markets_file = Path("markets.json")
    if markets_file.exists():
        markets = json.loads(markets_file.read_text(encoding="utf-8"))
        await db.seed_markets(markets)

    await db.ensure_day(now_local().date())

    application.job_queue.run_repeating(
        scheduler_tick,
        interval=CHECK_INTERVAL_SECONDS,
        first=3,
        name="result-scheduler",
    )

    log.info("Bot aktif.")


async def post_shutdown(application: Application) -> None:
    db = application.bot_data.get("db")
    if db:
        await db.close()


def build_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("hasil", cmd_hasil))
    application.add_handler(CommandHandler("update", cmd_update))
    application.add_handler(CommandHandler("pasaran", cmd_pasaran))

    application.add_handler(CallbackQueryHandler(
        hasil_callback,
        pattern=r"^hasil:",
    ))
    application.add_handler(CallbackQueryHandler(
        fill_callback,
        pattern=r"^fill:",
    ))
    application.add_handler(CallbackQueryHandler(
        save_callback,
        pattern=r"^save:",
    ))
    application.add_handler(CallbackQueryHandler(
        cancel_callback,
        pattern=r"^cancel:",
    ))

    application.add_handler(CallbackQueryHandler(
        update_page_callback,
        pattern=r"^updatepage:",
    ))
    application.add_handler(CallbackQueryHandler(
        update_pick_callback,
        pattern=r"^updatepick:",
    ))

    application.add_handler(CallbackQueryHandler(
        market_home_callback,
        pattern=r"^markethome$",
    ))
    application.add_handler(CallbackQueryHandler(
        market_add_callback,
        pattern=r"^marketadd$",
    ))
    application.add_handler(CallbackQueryHandler(
        market_category_callback,
        pattern=r"^marketcategory:",
    ))
    application.add_handler(CallbackQueryHandler(
        market_category_page_callback,
        pattern=r"^(marketeditpage|marketdisablepage|marketenablepage):",
    ))
    application.add_handler(CallbackQueryHandler(
        market_toggle_callback,
        pattern=r"^(marketdisable|marketenable):",
    ))
    application.add_handler(CallbackQueryHandler(
        market_edit_pick_callback,
        pattern=r"^marketeditpick:",
    ))
    application.add_handler(CallbackQueryHandler(
        market_field_callback,
        pattern=r"^marketfield:",
    ))
    application.add_handler(CallbackQueryHandler(
        market_list_callback,
        pattern=r"^marketlist:",
    ))
    application.add_handler(CallbackQueryHandler(
        noop_callback,
        pattern=r"^noop$",
    ))

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        text_router,
    ))

    return application


if __name__ == "__main__":
    build_application().run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
