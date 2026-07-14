# Result Bot — Versi Flat Railway

Semua file Python berada di root repository. Tidak ada folder `services` atau `handlers`, sehingga error:

```text
ModuleNotFoundError: No module named 'services'
```

tidak akan terjadi lagi.

## Pengaturan yang sudah dimasukkan

```text
Chat ID : -1004405531074
Topic ID: 35
Timezone: Asia/Jakarta
```

## Sebelum upload

Buka `config.py`, lalu isi:

```python
BOT_TOKEN = "ISI_BOT_TOKEN_DI_SINI"
```

## PostgreSQL Railway

Project harus mempunyai dua service:

```text
worker
Postgres
```

Pada service `worker` → `Variables`, tambahkan:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Jika nama service database berbeda, sesuaikan kata `Postgres`.

## Command

```text
/hasil
/update
/pasaran
```

`/cek` tidak digunakan oleh bot ini.

## Upload GitHub yang benar

Isi repository harus langsung seperti ini:

```text
bot.py
config.py
database.py
scheduler.py
utils.py
markets.json
requirements.txt
Procfile
railway.json
```

Jangan upload folder pembungkus seperti:

```text
resultbot-final-flat/bot.py
```

File `bot.py` harus terlihat langsung di halaman utama repository GitHub.

## Railway

- Gunakan 1 replica.
- Bot harus menjadi admin grup.
- Bot perlu izin mengirim dan menghapus pesan.
