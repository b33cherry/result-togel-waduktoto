# Bot Result Telegram — Topic RESULT TOGEL

Sudah dikunci ke:

- Chat ID: `-1004405531074`
- Topic / message_thread_id: `35`
- Zona waktu: `Asia/Jakarta`
- Reminder: setiap 5 menit

## Sebelum upload ke GitHub

Buka `bot.py`, lalu ganti:

```python
BOT_TOKEN = "ISI_BOT_TOKEN_DI_SINI"
```

menjadi token BotFather milik Anda.

## Railway

1. Upload semua file ke repository GitHub.
2. Buat project Railway dari repository tersebut.
3. Tambahkan PostgreSQL pada project Railway yang sama.
4. Di service bot, buat Variable:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

5. Pastikan service bot hanya memakai **1 replica**.
6. Masukkan bot ke grup dan jadikan admin, minimal dapat:
   - mengirim pesan;
   - menghapus pesan;
   - mengedit pesan bot.

## Perintah di Topic RESULT TOGEL

- `/cek` — pilihan RESULT HARI INI, BELUM DIISI, BELUM WAKTUNYA, OFF HARI INI.
- `/update` — memperbaiki result yang sudah diisi hari ini.
- `/pasaran` — tambah, edit, nonaktifkan, aktifkan kembali, dan melihat daftar pasaran.
- `/reload` — memuat ulang isi `markets.json`.

## Flow result

1. Saat jam buka tiba, bot mengirim alarm.
2. Staf klik `ISI RESULT`.
3. Staf mengirim angka atau `OFF`.
4. Bot meminta konfirmasi.
5. Setelah disimpan:
   - pesan input staf dihapus;
   - pesan konfirmasi dihapus;
   - reminder aktif dihapus;
   - pesan alarm berubah menjadi hasil final.

Format final:

```text
📢 RESULT PASARAN

📍 SINGAPORE (17:45)
🟢 RESULT : 1230

👤 @Admin 🕒 17:46
```

Jika OFF:

```text
📢 RESULT PASARAN

📍 SINGAPORE (17:45)
⚫ RESULT : OFF HARI INI

👤 @Admin 🕒 17:46
```

## Menambah atau mengubah pasaran

Gunakan `/pasaran` di Topic RESULT TOGEL. Data tersimpan di PostgreSQL sehingga tidak hilang saat Railway redeploy.

Pasaran awal juga tersedia di `markets.json`. File ini hanya menjadi data awal/fallback.
