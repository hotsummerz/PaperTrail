import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

NEO4J_URI = os.getenv("NEO4J_URI", "")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")

# Isi di .env, pisahkan dengan koma. Contoh: ALLOWED_USER_IDS=12345,67890
# Kosong = semua user boleh pakai bot.
ALLOWED_USER_IDS = {
    int(x)
    for x in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
}


def validate_bot_config():
    missing = []

    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")

    if missing:
        raise RuntimeError(
            "Konfigurasi belum lengkap: " + ", ".join(missing)
        )


def validate_neo4j_config():
    missing = []

    if not NEO4J_URI:
        missing.append("NEO4J_URI")

    if not NEO4J_USERNAME:
        missing.append("NEO4J_USERNAME")

    if not NEO4J_PASSWORD:
        missing.append("NEO4J_PASSWORD")

    if missing:
        raise RuntimeError(
            "Konfigurasi Neo4j belum lengkap: "
            + ", ".join(missing)
        )
    
# nama topik -> parent (None kalau tidak punya)
TOPICS = {
    "HCI": None,
    "AI": None,
    "Machine Learning": "AI",
    "Computer Network": None,
    "Software Engineering": None,
    "Security": None,
    "Data & Database": None,
    "Cloud & Infrastructure": None,
    "Lainnya": None,
}