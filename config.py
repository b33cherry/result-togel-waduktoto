from __future__ import annotations
import os
from zoneinfo import ZoneInfo

# ============================================================
# ISI TOKEN BOT DI SINI
# ============================================================
BOT_TOKEN = "ISI_BOT_TOKEN_DI_SINI"

GROUP_CHAT_ID = -1004405531074
MESSAGE_THREAD_ID = 35

DATABASE_URL = os.getenv("DATABASE_URL", "")
TIMEZONE = ZoneInfo("Asia/Jakarta")

REMINDER_INTERVAL_MINUTES = 5
CHECK_INTERVAL_SECONDS = 30
SESSION_TIMEOUT_MINUTES = 2

# Kosong = semua admin grup boleh.
# Contoh khusus owner: {123456789, 987654321}
OWNER_USER_IDS: set[int] = set()
