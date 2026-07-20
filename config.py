import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MY_TELEGRAM_ID  = int(os.getenv("MY_TELEGRAM_ID", "0"))
DATABASE_URL    = os.getenv("DATABASE_URL", "")

# groq/compound удалён — не поддерживает tool calling
MODELS = [
    "llama-3.3-70b-versatile",  # Primary: надёжный tool calling
    "llama-3.1-8b-instant",     # Fallback
]
MODEL = MODELS[0]

WHISPER_MODEL    = "whisper-large-v3-turbo"
HISTORY_LIMIT    = 6
SUMMARY_INTERVAL = 20
