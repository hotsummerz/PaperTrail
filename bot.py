import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    ContextTypes,
    filters,
)

from db import (
    check_neo4j,
    init_schema,
    save_paper,
    list_saved,
    list_saved_grouped,
    topic_counts,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    TOPICS,
    validate_bot_config,
)

from llm import OFF_TOPIC, ask_gemini, make_search_query, summarize_findings
from search import search_papers


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
# httpx mencetak URL request yang berisi bot token, jadi dibisukan.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("papertrail")
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

MAX_HISTORY_MESSAGES = 6  # 3 putaran tanya-jawab


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Pecah teks per paragraf supaya tidak terpotong di tengah kalimat."""
    parts = []
    current = ""

    for paragraph in text.split("\n\n"):
        # Paragraf tunggal yang terlalu panjang dipotong paksa.
        while len(paragraph) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(paragraph[:limit])
            paragraph = paragraph[limit:]

        candidate = f"{current}\n\n{paragraph}" if current else paragraph

        if len(candidate) > limit:
            parts.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        parts.append(current)

    return parts


async def keep_typing(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    task,
):
    """Menampilkan indikator typing selama task berjalan."""

    async def send_typing():
        while True:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id,
                action=ChatAction.TYPING,
            )
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(send_typing())

    try:
        return await task
    finally:
        typing_task.cancel()

        try:
            await typing_task
        except asyncio.CancelledError:
            pass


async def access_guard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Batasi akses ke user yang ada di ALLOWED_USER_IDS (jika diisi)."""
    if not ALLOWED_USER_IDS:
        return

    user = update.effective_user

    if not user or user.id not in ALLOWED_USER_IDS:
        if update.message:
            await update.message.reply_text(
                "Maaf, kamu belum diizinkan memakai bot ini."
            )
        raise ApplicationHandlerStop


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = (
        "Halo! Aku PaperTrail 📚\n\n"
        "Aku membantu pencarian dan pemahaman paper akademik.\n\n"
        "Fitur yang tersedia:\n"
        "• Search paper akademik\n"
        "• Menampilkan metadata publikasi\n"
        "• Informasi penulis, tahun, dan DOI\n"
        "• Ringkasan temuan utama dari abstrak\n"
        "• Tanya jawab berdasarkan paper yang ditemukan\n"
        "• Pemeriksaan koneksi Neo4j\n\n"
        "Gunakan /search untuk mencari paper.\n"
        "Contoh: /search usability testing e-wallet"
    )

    await update.message.reply_text(message)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = (
        "Perintah yang tersedia:\n"
        "/start - Mulai bot\n"
        "/help - Bantuan\n"
        "/search <kata kunci> - Mencari paper akademik\n"
        "/reset - Hapus riwayat percakapan\n"
        "/health - Periksa status dasar bot\n"
        "/dbcheck - Periksa koneksi Neo4j\n"
        "/saved - Menampilkan semua paper tersimpan\n"
        "/saved <topik> - Menampilkan paper tersimpan untuk topik tertentu\n"
        "/topics - Menampilkan jumlah paper tersimpan per topik\n\n"
        "Contoh perintah:\n"
        "/search usability testing e-wallet\n"
        "/topics AI\n"
        "Contoh pertanyaan:\n"
        "Apa perbedaan metode penelitian dalam paper AI fairness?"
    )

    await update.message.reply_text(message)


async def reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    context.user_data["history"] = []
    await update.message.reply_text("Riwayat percakapan dihapus.")


async def health(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "Bot Telegram aktif. Pemeriksaan koneksi Gemini "
        "dan Neo4j dilakukan terpisah."
    )


async def dbcheck(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text("Sedang memeriksa koneksi Neo4j...")

    try:
        result = await keep_typing(
            update,
            context,
            asyncio.to_thread(check_neo4j),
        )

        await update.message.reply_text(result)

    except Exception:
        logger.exception("Neo4j error")

        await update.message.reply_text(
            "Gagal memeriksa koneksi Neo4j. "
            "Periksa konfigurasi dan terminal aplikasi."
        )


OFF_TOPIC_REPLY = (
    "Maaf, aku khusus membantu pencarian dan analisis literatur "
    "akademik. Coba tanyakan tentang paper, metode penelitian, "
    "DOI, atau literature review."
)


async def answer_question(
    question: str,
    history: list[dict],
) -> tuple[str, bool]:
    """Return (jawaban, on_topic)."""
    query = await make_search_query(question, history)

    if query == OFF_TOPIC:
        return OFF_TOPIC_REPLY, False

    try:
        papers = await search_papers(query, limit=5)
    except Exception:
        logger.exception("OpenAlex error")
        papers = []

    answer = await ask_gemini(question, papers=papers, history=history)

    return answer, True


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()
    history = context.user_data.setdefault("history", [])

    try:
        answer, on_topic = await keep_typing(
            update,
            context,
            answer_question(question, history),
        )

        # Pesan off-topic tidak dimasukkan ke riwayat.
        if on_topic:
            history.append({"role": "user", "text": question})
            history.append({"role": "model", "text": answer})
            del history[:-MAX_HISTORY_MESSAGES]

        for part in split_message(answer):
            await update.message.reply_text(part)

    except Exception:
        logger.exception("Gemini error")

        await update.message.reply_text(
            "Maaf, terjadi masalah saat menghubungi Gemini. "
            "Periksa konfigurasi dan terminal aplikasi."
        )

async def search_and_summarize(query: str):
    papers = await search_papers(query, limit=5)

    if not papers:
        return papers, [], []

    findings, topics = await summarize_findings(papers)

    return papers, findings, topics


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.args:
        await update.message.reply_text(
            "Masukkan kata kunci pencarian.\n"
            "Contoh: /search usability testing e-wallet"
        )
        return

    query = " ".join(context.args)

    await update.message.reply_text(
        f"Sedang mencari paper untuk: {query}..."
    )

    try:
        papers, findings, topics = await keep_typing(
            update,
            context,
            search_and_summarize(query),
        )

        if not papers:
            await update.message.reply_text(
                "Tidak ditemukan paper yang cocok."
            )
            return

        # Disimpan sementara supaya tombol Simpan / perintah /save bisa pakai.
        context.user_data["last_results"] = [
            {"paper": p, "finding": f, "topics": t}
            for p, f, t in zip(papers, findings, topics)
        ]

        results = []

        for i, (paper, finding, topic) in enumerate(
            zip(papers, findings, topics), start=1
        ):
            title = paper.get("title") or "Belum tersedia"
            year = paper.get("publication_year") or "Belum tersedia"
            doi = paper.get("doi") or "Belum tersedia"
            citations = paper.get("cited_by_count", 0)

            authors = [
                item.get("author", {}).get("display_name", "")
                for item in (paper.get("authorships") or [])
                if item.get("author", {}).get("display_name")
            ]

            if authors:
                author_text = ", ".join(authors[:3])
                if len(authors) > 3:
                    author_text += " dkk."
            else:
                author_text = "Belum tersedia"

            results.append(
                f"Paper {i}\n"
                f"Judul: {title}\n"
                f"Penulis: {author_text}\n"
                f"Tahun: {year}\n"
                f"Sitasi: {citations}\n"
                f"Topik: {', '.join(topic)}\n"
                f"Temuan utama: {finding}\n"
                f"DOI: {doi}"
            )

        for part in split_message("\n\n".join(results)):
            await update.message.reply_text(part)

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Simpan {i}", callback_data=f"save:{i}"
                    )
                    for i in range(1, len(papers) + 1)
                ],
                [
                    InlineKeyboardButton(
                        "Simpan semua", callback_data="save:all"
                    )
                ],
            ]
        )

        await update.message.reply_text(
            "Simpan ke Neo4j?", reply_markup=keyboard
        )

    except Exception:
        logger.exception("Search error")

        await update.message.reply_text(
            "Terjadi kesalahan saat mencari paper. "
            "Silakan coba lagi."
        )

async def save_results(
    context: ContextTypes.DEFAULT_TYPE,
    indexes: list[int],
) -> str:
    """Simpan hasil pencarian terakhir nomor `indexes` (mulai dari 1)."""
    results = context.user_data.get("last_results") or []

    if not results:
        return "Belum ada hasil pencarian. Jalankan /search dulu."

    saved, existing, failed = [], [], []

    for i in indexes:
        if not 1 <= i <= len(results):
            continue

        item = results[i - 1]

        try:
            is_new = await asyncio.to_thread(
                save_paper,
                item["paper"],
                item["finding"],
                item["topics"],
            )
            (saved if is_new else existing).append(i)

        except Exception:
            logger.exception("Save error")
            failed.append(i)

    lines = []

    if saved:
        lines.append("Tersimpan: paper " + ", ".join(map(str, saved)))
    if existing:
        lines.append(
            "Sudah ada (diperbarui): paper " + ", ".join(map(str, existing))
        )
    if failed:
        lines.append(
            "Gagal: paper " + ", ".join(map(str, failed))
            + ". Cek /dbcheck."
        )

    return "\n".join(lines) or "Nomor paper tidak valid."


async def save_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    target = query.data.split(":", 1)[1]
    results = context.user_data.get("last_results") or []

    if target == "all":
        indexes = list(range(1, len(results) + 1))
    else:
        indexes = [int(target)]

    text = await save_results(context, indexes)
    await query.message.reply_text(text)


async def save_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    results = context.user_data.get("last_results") or []

    if context.args and context.args[0].lower() == "all":
        indexes = list(range(1, len(results) + 1))
    else:
        indexes = [int(a) for a in context.args if a.isdigit()]

    if not indexes:
        await update.message.reply_text(
            "Contoh: /save 1 3 atau /save all"
        )
        return

    await update.message.reply_text(await save_results(context, indexes))


async def saved_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    topic = " ".join(context.args) or None

    try:
        if topic:
            papers = await asyncio.to_thread(list_saved, topic)
            groups = (
                [{"topic": topic, "papers": papers}] if papers else []
            )
        else:
            groups = await asyncio.to_thread(list_saved_grouped)

    except Exception:
        logger.exception("List saved error")
        await update.message.reply_text(
            "Gagal membaca dari Neo4j. Cek /dbcheck."
        )
        return

    if not groups:
        await update.message.reply_text("Belum ada paper tersimpan.")
        return

    blocks = []

    for group in groups:
        papers = group["papers"]
        lines = [f"[{group['topic']}] ({len(papers)} paper)"]

        for i, p in enumerate(papers, start=1):
            lines.append(
                f"{i}. {p['title']} ({p['year'] or '-'})\n"
                f"Temuan: {p['finding'] or '-'}\n"
                f"DOI: {p['doi'] or 'Belum tersedia'}"
            )

        blocks.append("\n\n".join(lines))

    for part in split_message("\n\n".join(blocks)):
        await update.message.reply_text(part)

async def topics_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    arg = " ".join(context.args).strip()

    # Tanpa argumen: ringkasan semua topik.
    if not arg:
        try:
            rows = await asyncio.to_thread(topic_counts)
        except Exception:
            logger.exception("Topic count error")
            await update.message.reply_text(
                "Gagal membaca dari Neo4j. Cek /dbcheck."
            )
            return

        lines = [f"{r['topic']}: {r['total']}" for r in rows if r["total"]]

        await update.message.reply_text(
            "\n".join(lines) or "Belum ada paper tersimpan."
        )
        return

    # Dengan argumen: cocokkan ke daftar topik (huruf besar/kecil bebas).
    name = next((t for t in TOPICS if t.lower() == arg.lower()), None)

    if name is None:
        await update.message.reply_text(
            f'Topik "{arg}" tidak dikenal.\n'
            f"Pilihan: {', '.join(TOPICS)}"
        )
        return

    try:
        papers = await asyncio.to_thread(list_saved, name, 1000)
    except Exception:
        logger.exception("Topic detail error")
        await update.message.reply_text(
            "Gagal membaca dari Neo4j. Cek /dbcheck."
        )
        return

    if not papers:
        await update.message.reply_text(f"Topik {name} masih kosong.")
        return

    lines = [f"{name}: {len(papers)} paper"]
    lines += [
        f"{i}. {p['title']} ({p['year'] or '-'})"
        for i, p in enumerate(papers, start=1)
    ]

    for part in split_message("\n".join(lines)):
        await update.message.reply_text(part)

def main():
    validate_bot_config()

    try:
        init_schema()

    except Exception:
        logger.exception(
            "Init skema Neo4j gagal. Fitur simpan mungkin tidak jalan."
        )

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # group=-1 jalan lebih dulu dari handler lain.
    app.add_handler(TypeHandler(Update, access_guard), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("dbcheck", dbcheck))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(CommandHandler("saved", saved_command))
    app.add_handler(CommandHandler("topics", topics_command))
    app.add_handler(CallbackQueryHandler(save_callback, pattern=r"^save:"))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    logger.info("PaperTrail Agent sedang berjalan...")

    app.run_polling()


if __name__ == "__main__":
    main()