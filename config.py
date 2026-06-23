import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MY_TELEGRAM_ID  = int(os.getenv("MY_TELEGRAM_ID", "0"))
DATABASE_URL    = os.getenv("DATABASE_URL", "")   # Railway задаёт автоматически

# Model fallback chain — fastest/cheapest first.
MODELS = [
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
]
MODEL = MODELS[0]

HISTORY_LIMIT    = 6
SUMMARY_INTERVAL = 20
