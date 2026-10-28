import asyncio
import json
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, TOPICS
from search import rebuild_abstract


client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)


SYSTEM_PROMPT = """
Kamu adalah PaperTrail Agent, asisten untuk literatur akademik.

SCOPE YANG DIIZINKAN:
- Pencarian dan pemahaman paper akademik.
- Metode penelitian, objek penelitian, dan temuan.
- Literature matrix dan ringkasan paper.
- DOI, metadata publikasi, sitasi, dan relasi antarpaper.
- Perbandingan penelitian dan sintesis literatur.

Jika pengguna meminta hal yang tidak berkaitan dengan
literatur akademik, tolak dengan sopan dan jelaskan scope bot.

SUMBER DATA:
- Pesan pengguna bisa disertai blok DATA PAPER hasil pencarian
  OpenAlex. Itu satu-satunya sumber paper yang boleh kamu rujuk.
- Rujuk paper dengan nomornya (Paper 1, Paper 2, dst).
- Jika DATA PAPER kosong atau tidak relevan, katakan terus terang
  bahwa tidak ada paper yang ditemukan. Jangan menebak.

ATURAN:
- Jangan mengarang judul, penulis, DOI, tahun, atau temuan.
- Hanya simpulkan temuan dari abstrak yang tersedia. Jika abstrak
  tidak ada, katakan abstraknya belum tersedia.
- Pertanyaan konseptual tentang metode atau istilah akademik boleh
  dijawab dari pengetahuan umum, tapi jangan sebut itu sebagai isi
  paper.
- Pertanyaan di luar literatur akademik (resep, hewan, hiburan, dll.)
  tolak singkat tanpa menjawab isinya, dan jangan mengaitkannya
  dengan paper yang tidak relevan.
- Jawab dalam bahasa Indonesia secara ringkas.

FORMAT JAWABAN:
- Gunakan teks biasa (plain text), bukan Markdown.
- Jangan gunakan simbol Markdown seperti *, **, #, ##, atau backtick.
- Jika perlu daftar, gunakan angka (1. 2. 3.) atau kalimat biasa.
- Gunakan paragraf pendek dan spasi antarparagraf.
- Hindari emoji dan simbol dekoratif.
"""


def clean_response(text: str) -> str:
    # Heading Markdown
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)

    # Bullet "* item" atau "- item" jadi "- item"
    text = re.sub(r"(?m)^\s*[*-]\s+", "- ", text)

    # Bold / italic
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)

    # Backtick
    text = text.replace("```", "").replace("`", "")

    return text.strip()


def format_papers(papers: list[dict]) -> str:
    if not papers:
        return "(tidak ada paper ditemukan)"

    blocks = []

    for i, paper in enumerate(papers, start=1):
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (paper.get("authorships") or [])
            if a.get("author", {}).get("display_name")
        ]

        author_text = ", ".join(authors[:5]) or "tidak tersedia"

        if len(authors) > 5:
            author_text += " dkk."

        abstract = rebuild_abstract(
            paper.get("abstract_inverted_index")
        )[:1500] or "tidak tersedia"

        blocks.append(
            f"Paper {i}\n"
            f"Judul: {paper.get('title') or 'tidak tersedia'}\n"
            f"Penulis: {author_text}\n"
            f"Tahun: {paper.get('publication_year') or 'tidak tersedia'}\n"
            f"Sitasi: {paper.get('cited_by_count', 0)}\n"
            f"DOI: {paper.get('doi') or 'tidak tersedia'}\n"
            f"Abstrak: {abstract}"
        )

    return "\n\n".join(blocks)


OFF_TOPIC = "OFF_TOPIC"


async def make_search_query(
    question: str,
    history: list[dict] | None = None,
) -> str:
    """Ubah pesan jadi kata kunci pencarian, atau OFF_TOPIC kalau
    pesan tidak berkaitan dengan literatur akademik."""
    if client is None:
        return question

    recent = "\n".join(
        f"{'Pengguna' if t['role'] == 'user' else 'Bot'}: {t['text'][:300]}"
        for t in (history or [])[-4:]
    ) or "(kosong)"

    try:
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=(
                "Kamu menyaring pesan untuk bot pencari literatur "
                "akademik.\n\n"
                "Jika pesan terakhir BUKAN tentang literatur akademik "
                "(paper, jurnal, metode penelitian, temuan, sitasi, DOI, "
                "topik riset) dan bukan lanjutan dari percakapan akademik "
                f"di bawah, balas HANYA dengan {OFF_TOPIC}. Contoh "
                "OFF_TOPIC: resep masakan, hewan peliharaan, hiburan, "
                "obrolan umum, pengetahuan umum non-akademik.\n\n"
                "Jika akademik, ubah menjadi kata kunci pencarian paper "
                "dalam bahasa Inggris, maksimal 8 kata, dan balas HANYA "
                "dengan kata kuncinya. Untuk pesan lanjutan, ambil topik "
                "dari percakapan sebelumnya.\n\n"
                f"Percakapan sebelumnya:\n{recent}\n\n"
                f"Pesan terakhir: {question}"
            ),
            config=types.GenerateContentConfig(temperature=0),
        )

        query = (response.text or "").strip().strip('"')

        if OFF_TOPIC in query.upper():
            return OFF_TOPIC

        return query[:100] or question

    except Exception as error:
        print(f"Query rewrite error: {error}")
        return question


async def ask_gemini(
    question: str,
    papers: list[dict] | None = None,
    history: list[dict] | None = None,
) -> str:
    if client is None:
        raise RuntimeError(
            "GEMINI_API_KEY belum diatur di file .env."
        )

    contents = []

    for turn in history or []:
        contents.append(
            types.Content(
                role=turn["role"],
                parts=[types.Part(text=turn["text"])],
            )
        )

    user_text = question

    if papers is not None:
        user_text = (
            f"DATA PAPER (hasil OpenAlex):\n{format_papers(papers)}\n\n"
            f"Pertanyaan pengguna:\n{question}"
        )

    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(text=user_text)],
        )
    )

    response = await client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.3,
        ),
    )

    answer = response.text

    if not answer or not answer.strip():
        return "Maaf, Gemini belum menghasilkan jawaban."

    return clean_response(answer)

async def summarize_findings(
    papers: list[dict],
) -> tuple[list[str], list[list[str]]]:
    """Ringkas temuan + klasifikasi topik tiap paper (1 call Gemini)."""
    no_abstract = "Abstrak belum tersedia, temuan belum bisa diringkas."
    findings = [no_abstract] * len(papers)
    topics = [["Lainnya"] for _ in papers]

    if client is None:
        return findings, topics

    items = []

    for i, paper in enumerate(papers):
        abstract = rebuild_abstract(
            paper.get("abstract_inverted_index")
        )[:1500]

        if abstract:
            items.append(
                {
                    "index": i,
                    "judul": paper.get("title") or "",
                    "abstrak": abstract,
                }
            )

    if not items:
        return findings, topics

    for item in items:
        findings[item["index"]] = "Temuan belum bisa diringkas."

    prompt = (
        "Untuk setiap paper berikut, tulis temuan utama dalam "
        "1 sampai 2 kalimat bahasa Indonesia, hanya berdasarkan "
        "abstrak yang diberikan. Jangan menambah informasi di "
        "luar abstrak. Gunakan teks biasa tanpa Markdown.\n\n"
        "Pilih juga 1 sampai 3 topik untuk tiap paper, HANYA dari "
        f"daftar ini: {', '.join(TOPICS)}. "
        'Jika tidak ada yang cocok, pakai "Lainnya".\n\n'
        "Balas HANYA dengan JSON array berbentuk "
        '[{"index": 0, "temuan": "...", "topik": ["HCI"]}].\n\n'
        f"Data:\n{json.dumps(items, ensure_ascii=False)}"
    )

    models = [GEMINI_MODEL, "gemini-2.5-flash"]
    raw = ""

    for model_name in dict.fromkeys(models):
        for attempt in range(3):
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.2),
                )

                raw = response.text or ""

                start = min(
                    (p for p in (raw.find("["), raw.find("{")) if p != -1),
                    default=-1,
                )

                if start == -1:
                    raise ValueError("Tidak ada JSON di respons Gemini")

                data, _ = json.JSONDecoder().raw_decode(raw[start:])

                if isinstance(data, dict):
                    data = next(
                        (v for v in data.values() if isinstance(v, list)),
                        [],
                    )

                for entry in data:
                    idx = entry.get("index")
                    text = entry.get("temuan")

                    if not (isinstance(idx, int) and 0 <= idx < len(papers)):
                        continue

                    if text:
                        findings[idx] = clean_response(str(text))

                    raw_topics = entry.get("topik")

                    if isinstance(raw_topics, list):
                        # Buang topik di luar daftar (Gemini suka ngarang).
                        valid = [t for t in raw_topics if t in TOPICS][:3]

                        if valid:
                            topics[idx] = valid

                return findings, topics

            except Exception as error:
                message = str(error)
                temporary = (
                    "503" in message
                    or "429" in message
                    or "UNAVAILABLE" in message
                )

                print(
                    f"Summarize error ({model_name}, percobaan "
                    f"{attempt + 1}): {type(error).__name__}: {message[:200]}"
                )

                if raw:
                    print(f"Respons mentah Gemini: {raw[:500]!r}")

                if temporary and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                break

    return findings, topics