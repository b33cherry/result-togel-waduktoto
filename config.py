from __future__ import annotations

import os
from zoneinfo import ZoneInfo

# ============================================================
# WAJIB DIISI
# ============================================================
BOT_TOKEN = "8842731707:AAEE_OHzL-IN5NpsjXkOjDCF2aOtfBmF2OA"

# Grup dan topic RESULT TOGEL.
GROUP_CHAT_ID = -1004405531074
MESSAGE_THREAD_ID = 35

# Di Railway, buat variable:
# DATABASE_URL=${{Postgres.DATABASE_URL}}
DATABASE_URL = os.getenv("DATABASE_URL", "")

TIMEZONE = ZoneInfo("Asia/Jakarta")
REMINDER_INTERVAL_MINUTES = 5
CHECK_INTERVAL_SECONDS = 30
SESSION_TIMEOUT_MINUTES = 2

# Kosong berarti semua admin/owner grup boleh.
# Contoh membatasi hanya ID tertentu:
# OWNER_USER_IDS = {123456789}
OWNER_USER_IDS: set[int] = set()
