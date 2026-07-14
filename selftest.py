from pathlib import Path
import json
import py_compile

ROOT = Path(__file__).resolve().parent

required = {
    "bot.py", "config.py", "database.py", "scheduler.py", "utils.py",
    "markets.json", "requirements.txt", "Procfile", "railway.json"
}
missing = required - {p.name for p in ROOT.iterdir()}
assert not missing, f"File kurang: {sorted(missing)}"

for file in ROOT.glob("*.py"):
    py_compile.compile(str(file), doraise=True)

markets = json.loads((ROOT / "markets.json").read_text(encoding="utf-8"))
assert len(markets) == 42, f"Jumlah pasaran harus 42, ditemukan {len(markets)}"
assert len({m["slug"] for m in markets}) == len(markets), "Slug pasaran harus unik"

database_code = (ROOT / "database.py").read_text(encoding="utf-8")
assert "::text::time" in database_code, "Binding jam PostgreSQL belum aman"
assert "$3::time" not in database_code, "Masih ada binding string langsung ke TIME"

bot_code = (ROOT / "bot.py").read_text(encoding="utf-8")
assert 'CommandHandler("hasil"' in bot_code, "Command /hasil tidak ditemukan"
assert "inline_keyboard.append" not in bot_code, "Inline keyboard masih dimutasi langsung"

print("SELF-TEST OK")
print(f"Pasaran: {len(markets)}")
