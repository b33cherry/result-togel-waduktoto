from __future__ import annotations

from datetime import datetime, timedelta

from config import (
    REMINDER_INTERVAL_MINUTES,
    TIMEZONE,
)
from utils import (
    alarm_text,
    fill_keyboard,
    now_local,
    reminder_text,
    safe_delete,
    send_topic,
)


async def scheduler_tick(context) -> None:
    db = context.application.bot_data["db"]
    current = now_local()
    today = current.date()

    await db.ensure_day(today)
    rows = await db.today_rows(today)

    for row in rows:
        if row["status"] in ("DONE", "OFF_MANUAL", "OFF_SCHEDULE"):
            continue

        due = datetime.combine(today, row["open_time"], tzinfo=TIMEZONE)
        if current < due:
            continue

        if not row["main_message_id"]:
            message = await send_topic(
                context,
                alarm_text(row),
                parse_mode="HTML",
                reply_markup=fill_keyboard(row["slug"]),
            )
            await db.mark_pending(row["id"], message.message_id)
            row = await db.daily_by_slug(today, row["slug"])

        elif row["status"] == "WAITING":
            await db.mark_pending(
                row["id"],
                row["main_message_id"],
            )
            row = await db.daily_by_slug(today, row["slug"])

        late_minutes = max(
            0,
            int((current - due).total_seconds() // 60),
        )

        if late_minutes < REMINDER_INTERVAL_MINUTES:
            continue

        last_reminder = row["last_reminder_at"]
        if last_reminder:
            elapsed = current - last_reminder.astimezone(TIMEZONE)
            if elapsed < timedelta(minutes=REMINDER_INTERVAL_MINUTES):
                continue

        await safe_delete(
            context.bot,
            row["reminder_message_id"],
        )

        reminder = await send_topic(
            context,
            reminder_text(row, late_minutes),
            parse_mode="HTML",
            reply_markup=fill_keyboard(row["slug"]),
        )
        await db.set_reminder(
            row["id"],
            reminder.message_id,
            current,
        )
