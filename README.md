# Result Bot v2

Sudah diarahkan ke:

- Chat ID: `-1004405531074`
- Topic ID: `35`
- Timezone: `Asia/Jakarta`

## 1. Isi token bot

Buka `config.py`:

```python
BOT_TOKEN = "ISI_BOT_TOKEN_DI_SINI"
```

## 2. Tambahkan PostgreSQL di Railway

Dalam project Railway:

1. Klik `+ New`
2. Pilih `Database`
3. Pilih `PostgreSQL`
4. Masuk ke service `worker`
5. Buka `Variables`
6. Tambahkan:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Jika service database namanya bukan `Postgres`, sesuaikan namanya.

## Command

- `/cek`
- `/update`
- `/pasaran`

## Flow

Alarm:

```text
⏰ WAKTUNYA RESULT

📍 SINGAPORE (17:45)
🟡 STATUS : MENUNGGU RESULT
```

Setelah klik `ISI RESULT`:

```text
✍️ MASUKKAN RESULT

📍 SINGAPORE (17:45)

Jika pasaran libur cukup ketik: OFF
```

Hasil final:

```text
📢 RESULT PASARAN

📍 SINGAPORE (17:45)
🟢 RESULT : 1230

👤 @Admin 🕒 17:46
```

## Penting

Gunakan hanya **1 replica** pada service worker agar reminder tidak ganda.
