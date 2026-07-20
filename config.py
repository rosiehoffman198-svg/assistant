import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MY_TELEGRAM_ID  = int(os.getenv("MY_TELEGRAM_ID", "0"))
DATABASE_URL    = os.getenv("DATABASE_URL", "")

MODELS = [
    "groq/compound",            # Primary: Groq-нативная модель для агентов и tool calling
    "llama-3.3-70b-versatile",  # Fallback 1: проверенный
    "llama-3.1-8b-instant",     # Fallback 2: быстрый
]
MODEL = MODELS[0]

WHISPER_MODEL    = "whisper-large-v3-turbo"  # быстрее чем large-v3, качество то же
HISTORY_LIMIT    = 6
SUMMARY_INTERVAL = 20
