# PaperTrail

Bot Telegram untuk mencari paper akademik, meringkas temuan utamanya, dan menyimpannya ke graph database Neo4j yang dikelompokkan per topik.

## Fitur

- Pencarian paper akademik lewat OpenAlex (`/search`)
- Ringkasan temuan utama dari abstrak, ditulis oleh Gemini
- Klasifikasi topik otomatis (HCI, AI, Machine Learning, Computer Network, dll.)
- Simpan paper ke Neo4j lewat tombol inline atau perintah `/save`
- Lihat koleksi paper, dikelompokkan per topik (`/saved`, `/topics`)
- Tanya jawab seputar literatur akademik berdasarkan hasil pencarian
- Pembatasan akses berdasarkan Telegram user ID (opsional)

## Cara kerja

```
Pesan Telegram
      |
      v
  bot.py ----> search.py ----> OpenAlex API
      |
      +------> llm.py ----> Gemini (temuan utama + topik)
      |
      +------> db.py ----> Neo4j (simpan dan baca paper)
```

1. `/search` mengambil 5 paper dari OpenAlex.
2. Gemini meringkas temuan tiap paper dan memilih 1-3 topik dari daftar `TOPICS` di `config.py`, dalam satu panggilan.
3. Hasil ditampilkan bersama tombol Simpan.
4. Paper yang disimpan masuk ke Neo4j beserta penulis dan topiknya.

## Model graf

```
(:Author)-[:AUTHORED]->(:Paper)-[:HAS_TOPIC]->(:Topic)
(:Topic)-[:SUBTOPIC_OF]->(:Topic)
```

- `Paper`: `openalex_id`, `title`, `year`, `doi`, `cited_by`, `abstract`, `finding`, `saved_at`
- `Author`: `id`, `name`
- `Topic`: `name`
- `SUBTOPIC_OF` membuat filter topik induk ikut menyertakan subtopiknya. Contoh: `/saved AI` juga menampilkan paper Machine Learning.

## Kebutuhan

- Python 3.10 atau lebih baru
- Token bot Telegram dari [@BotFather](https://t.me/BotFather)
- API key Gemini dari [Google AI Studio](https://aistudio.google.com/)
- Instance Neo4j (Aura Free sudah cukup)
- API key OpenAlex (opsional)

## Instalasi

```bash
git clone https://github.com/hotsummerz/PaperTrail.git
cd PaperTrail

python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

pip install python-telegram-bot google-genai neo4j httpx python-dotenv
```

## Konfigurasi

Buat file `.env` di root proyek:

```env
TELEGRAM_BOT_TOKEN=isi_token_bot
GEMINI_API_KEY=isi_api_key_gemini
GEMINI_MODEL=gemini-3.6-flash

NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=isi_password_neo4j
NEO4J_DATABASE=neo4j

OPENALEX_API_KEY=
ALLOWED_USER_IDS=
```

| Variabel | Wajib | Keterangan |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Ya | Token dari BotFather |
| `GEMINI_API_KEY` | Ya | API key Gemini |
| `GEMINI_MODEL` | Tidak | Default `gemini-3.6-flash` |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | Untuk fitur simpan | Kredensial Neo4j |
| `NEO4J_DATABASE` | Tidak | Default `neo4j` |
| `OPENALEX_API_KEY` | Tidak | Menaikkan batas request OpenAlex |
| `ALLOWED_USER_IDS` | Tidak | ID Telegram dipisah koma, contoh `12345,67890`. Kosong berarti semua user boleh memakai bot |

Jangan commit file `.env`. Pastikan sudah masuk `.gitignore`.

## Menjalankan

```bash
python bot.py
```

Saat start, bot membuat constraint dan node `Topic` di Neo4j secara otomatis. Aman dijalankan berulang kali.

## Perintah

| Perintah | Fungsi |
|---|---|
| `/start` | Mulai bot |
| `/help` | Daftar perintah |
| `/search <kata kunci>` | Cari paper, tampilkan temuan utama dan topik |
| `/save <nomor>` | Simpan paper dari hasil pencarian terakhir. Contoh: `/save 1 3` atau `/save all` |
| `/saved` | Semua paper tersimpan, dikelompokkan per topik |
| `/saved <topik>` | Paper tersimpan untuk satu topik. Contoh: `/saved HCI` |
| `/topics` | Jumlah paper per topik |
| `/topics <topik>` | Daftar judul paper dalam satu topik |
| `/reset` | Hapus riwayat percakapan |
| `/health` | Cek status dasar bot |
| `/dbcheck` | Cek koneksi Neo4j |

Selain perintah, kamu bisa bertanya langsung, misalnya: "Apa perbedaan metode penelitian dalam paper AI fairness?". Bot hanya melayani topik literatur akademik.

## Mengubah daftar topik

Edit dict `TOPICS` di `config.py`. Key adalah nama topik, value adalah topik induknya (atau `None`).

```python
TOPICS = {
    "HCI": None,
    "AI": None,
    "Machine Learning": "AI",
    "Computer Network": None,
    "Lainnya": None,
}
```

Restart bot supaya node topik baru dibuat.

## Struktur proyek

```
PaperTrail/
├── bot.py       # handler Telegram dan alur perintah
├── config.py    # env vars dan daftar topik
├── db.py        # koneksi dan query Neo4j
├── llm.py       # panggilan Gemini (ringkasan, topik, tanya jawab)
├── search.py    # klien OpenAlex
└── .env         # kredensial (tidak di-commit)
```

## Catatan

- Topik ditentukan Gemini dari abstrak, jadi bisa meleset. Paper tanpa abstrak masuk ke "Lainnya".
- Hasil pencarian terakhir disimpan di memori bot. Kalau bot restart, jalankan `/search` ulang sebelum menyimpan.
- Tombol Simpan merujuk ke pencarian terakhir, bukan ke pesan tempat tombol berada.