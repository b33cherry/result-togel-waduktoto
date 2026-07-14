from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import asyncpg


class Database:
    def __init__(self, url: str):
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        await self.init_schema()

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def init_schema(self) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                id BIGSERIAL PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                close_time TIME NOT NULL DEFAULT '00:00',
                open_time TIME NOT NULL,
                active_days JSONB NOT NULL DEFAULT '[]'::jsonb,
                note TEXT NOT NULL DEFAULT '',
                result_digits INTEGER NOT NULL DEFAULT 4,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS daily_results (
                id BIGSERIAL PRIMARY KEY,
                market_id BIGINT NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                result_date DATE NOT NULL,
                status TEXT NOT NULL DEFAULT 'WAITING',
                result_value TEXT,
                filled_by_id BIGINT,
                filled_by_name TEXT,
                filled_at TIMESTAMPTZ,
                main_message_id BIGINT,
                reminder_message_id BIGINT,
                last_reminder_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(market_id, result_date)
            );

            CREATE TABLE IF NOT EXISTS result_logs (
                id BIGSERIAL PRIMARY KEY,
                market_id BIGINT NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                result_date DATE NOT NULL,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                actor_id BIGINT,
                actor_name TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_daily_date_status
                ON daily_results(result_date, status);

            CREATE INDEX IF NOT EXISTS idx_daily_market_date
                ON daily_results(market_id, result_date);
            """)

    async def sync_markets(self, items: list[dict[str, Any]]) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for item in items:
                    await conn.execute("""
                        INSERT INTO markets
                            (slug, name, close_time, open_time, active_days,
                             note, result_digits, active)
                        VALUES
                            ($1, $2, $3::time, $4::time, $5::jsonb,
                             $6, $7, $8)
                        ON CONFLICT (slug) DO UPDATE SET
                            name = EXCLUDED.name,
                            close_time = EXCLUDED.close_time,
                            open_time = EXCLUDED.open_time,
                            active_days = EXCLUDED.active_days,
                            note = EXCLUDED.note,
                            result_digits = EXCLUDED.result_digits,
                            active = EXCLUDED.active,
                            updated_at = NOW()
                    """,
                    item["slug"],
                    item["name"],
                    item.get("close_time", "00:00"),
                    item["open_time"],
                    json.dumps(item.get("active_days", [0,1,2,3,4,5,6])),
                    item.get("note", ""),
                    int(item.get("result_digits", 4)),
                    bool(item.get("active", True)))

    async def ensure_day(self, target_date: date) -> None:
        assert self.pool
        weekday = target_date.weekday()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, active_days
                FROM markets
                WHERE active = TRUE
            """)
            async with conn.transaction():
                for row in rows:
                    days = row["active_days"]
                    if isinstance(days, str):
                        days = json.loads(days)
                    status = "WAITING" if weekday in days else "OFF_SCHEDULE"
                    await conn.execute("""
                        INSERT INTO daily_results(market_id, result_date, status)
                        VALUES($1, $2, $3)
                        ON CONFLICT(market_id, result_date) DO NOTHING
                    """, row["id"], target_date, status)

    async def get_today_rows(self, target_date: date):
        assert self.pool
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT d.*, m.slug, m.name, m.close_time, m.open_time,
                       m.note, m.result_digits, m.active_days, m.active
                FROM daily_results d
                JOIN markets m ON m.id = d.market_id
                WHERE d.result_date = $1 AND m.active = TRUE
                ORDER BY m.open_time, m.name
            """, target_date)

    async def get_row_by_slug(self, target_date: date, slug: str):
        assert self.pool
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT d.*, m.slug, m.name, m.close_time, m.open_time,
                       m.note, m.result_digits, m.active_days, m.active
                FROM daily_results d
                JOIN markets m ON m.id = d.market_id
                WHERE d.result_date = $1 AND m.slug = $2
            """, target_date, slug)

    async def mark_pending_and_main(self, daily_id: int, message_id: int) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE daily_results
                SET status='PENDING',
                    main_message_id=$2,
                    updated_at=NOW()
                WHERE id=$1 AND status='WAITING'
            """, daily_id, message_id)

    async def set_main_message(self, daily_id: int, message_id: int) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE daily_results
                SET main_message_id=$2, updated_at=NOW()
                WHERE id=$1
            """, daily_id, message_id)

    async def set_reminder(
        self,
        daily_id: int,
        message_id: int,
        sent_at: datetime,
    ) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE daily_results
                SET reminder_message_id=$2,
                    last_reminder_at=$3,
                    updated_at=NOW()
                WHERE id=$1
            """, daily_id, message_id, sent_at)

    async def clear_reminder(self, daily_id: int) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE daily_results
                SET reminder_message_id=NULL, updated_at=NOW()
                WHERE id=$1
            """, daily_id)

    async def save_result(
        self,
        target_date: date,
        slug: str,
        value: str,
        actor_id: int,
        actor_name: str,
        *,
        force_update: bool = False,
    ):
        assert self.pool
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    SELECT d.*, m.name, m.slug, m.open_time
                    FROM daily_results d
                    JOIN markets m ON m.id=d.market_id
                    WHERE d.result_date=$1 AND m.slug=$2
                    FOR UPDATE
                """, target_date, slug)
                if not row:
                    return None

                old_value = row["result_value"]
                if row["status"] == "DONE" and not force_update:
                    return {"error": "already_done", "row": row}

                status = "OFF_MANUAL" if value == "OFF" else "DONE"
                await conn.execute("""
                    UPDATE daily_results
                    SET status=$3,
                        result_value=$4,
                        filled_by_id=$5,
                        filled_by_name=$6,
                        filled_at=NOW(),
                        updated_at=NOW()
                    WHERE result_date=$1 AND market_id=$2
                """,
                target_date,
                row["market_id"],
                status,
                value,
                actor_id,
                actor_name)

                action = "UPDATE_RESULT" if old_value is not None else (
                    "SET_OFF" if value == "OFF" else "SET_RESULT"
                )
                await conn.execute("""
                    INSERT INTO result_logs
                        (market_id, result_date, action, old_value,
                         new_value, actor_id, actor_name)
                    VALUES($1,$2,$3,$4,$5,$6,$7)
                """,
                row["market_id"],
                target_date,
                action,
                old_value,
                value,
                actor_id,
                actor_name)
                return {"row": row, "old_value": old_value}

    async def list_markets(self, include_inactive: bool = True):
        assert self.pool
        clause = "" if include_inactive else "WHERE active = TRUE"
        async with self.pool.acquire() as conn:
            return await conn.fetch(f"""
                SELECT *
                FROM markets
                {clause}
                ORDER BY active DESC, open_time, name
            """)

    async def get_market(self, slug: str):
        assert self.pool
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM markets WHERE slug=$1
            """, slug)

    async def add_market(
        self,
        *,
        slug: str,
        name: str,
        open_time: str,
        active_days: list[int],
        result_digits: int = 4,
    ):
        assert self.pool
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                INSERT INTO markets
                    (slug, name, close_time, open_time, active_days,
                     note, result_digits, active)
                VALUES
                    ($1,$2,'00:00',$3::time,$4::jsonb,'',$5,TRUE)
                RETURNING *
            """,
            slug,
            name,
            open_time,
            json.dumps(active_days),
            result_digits)

    async def update_market(
        self,
        slug: str,
        *,
        name: str | None = None,
        open_time: str | None = None,
        active_days: list[int] | None = None,
        result_digits: int | None = None,
    ):
        assert self.pool
        fields = []
        values: list[Any] = []
        idx = 1

        if name is not None:
            fields.append(f"name=${idx}")
            values.append(name)
            idx += 1
        if open_time is not None:
            fields.append(f"open_time=${idx}::time")
            values.append(open_time)
            idx += 1
        if active_days is not None:
            fields.append(f"active_days=${idx}::jsonb")
            values.append(json.dumps(active_days))
            idx += 1
        if result_digits is not None:
            fields.append(f"result_digits=${idx}")
            values.append(result_digits)
            idx += 1

        if not fields:
            return await self.get_market(slug)

        fields.append("updated_at=NOW()")
        values.append(slug)
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(f"""
                UPDATE markets
                SET {", ".join(fields)}
                WHERE slug=${idx}
                RETURNING *
            """, *values)

    async def set_market_active(self, slug: str, active: bool):
        assert self.pool
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                UPDATE markets
                SET active=$2, updated_at=NOW()
                WHERE slug=$1
                RETURNING *
            """, slug, active)
