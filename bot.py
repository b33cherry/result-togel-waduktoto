from __future__ import annotations
import json
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)

from config import (
    BOT_TOKEN, DATABASE_URL,
    CHECK_INTERVAL_SECONDS
)
from database import Database
from services.scheduler import scheduler_tick
from handlers.check import cmd_cek, check_callback
from handlers.result import fill_callback, text_input, save_callback, cancel_callback
from handlers.update import cmd_update, page_callback, pick_callback
from handlers.markets import (
    cmd_pasaran, home_callback, add_start, text_wizard,
    category_callback, toggle_callback, edit_pick,
    field_callback, list_callback
)

async def noop(update, context):
    await update.callback_query.answer()

async def post_init(app):
    if BOT_TOKEN == "8842731707:AAEE_OHzL-IN5NpsjXkOjDCF2aOtfBmF2OA":
        raise RuntimeError("Isi BOT_TOKEN di config.py.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL belum tersedia. Tambahkan PostgreSQL di Railway.")

    db = Database(DATABASE_URL)
    await db.connect()
    app.bot_data["db"] = db

    p = Path("markets.json")
    if p.exists():
        await db.sync_seed_markets(json.loads(p.read_text(encoding="utf-8")))
    from services.common import now_local
    await db.ensure_day(now_local().date())

    app.job_queue.run_repeating(
        scheduler_tick,
        interval=CHECK_INTERVAL_SECONDS,
        first=3,
        name="result-scheduler"
    )

async def post_shutdown(app):
    db = app.bot_data.get("db")
    if db:
        await db.close()

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("pasaran", cmd_pasaran))

    app.add_handler(CallbackQueryHandler(check_callback, pattern=r"^check:"))
    app.add_handler(CallbackQueryHandler(fill_callback, pattern=r"^fill:"))
    app.add_handler(CallbackQueryHandler(save_callback, pattern=r"^save:"))
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel:"))

    app.add_handler(CallbackQueryHandler(page_callback, pattern=r"^updpage:"))
    app.add_handler(CallbackQueryHandler(pick_callback, pattern=r"^upd:"))

    app.add_handler(CallbackQueryHandler(home_callback, pattern=r"^market:home$"))
    app.add_handler(CallbackQueryHandler(add_start, pattern=r"^market:add$"))
    app.add_handler(CallbackQueryHandler(category_callback, pattern=r"^market:(edit|disable|enable):"))
    app.add_handler(CallbackQueryHandler(toggle_callback, pattern=r"^(mdisable|menable):"))
    app.add_handler(CallbackQueryHandler(edit_pick, pattern=r"^medit:"))
    app.add_handler(CallbackQueryHandler(field_callback, pattern=r"^mfield:"))
    app.add_handler(CallbackQueryHandler(list_callback, pattern=r"^market:list:"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))

    # Satu handler teks, lalu router internal berdasarkan state.
    async def text_router(update, context):
        state = context.user_data.get("state","")
        if state.startswith("market_"):
            return await text_wizard(update, context)
        return await text_input(update, context)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False
    )

if __name__ == "__main__":
    main()
