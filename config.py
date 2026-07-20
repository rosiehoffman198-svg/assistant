import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MY_TELEGRAM_ID  = int(os.getenv("MY_TELEGRAM_ID", "0"))
DATABASE_URL    = os.getenv("DATABASE_URL", "")

# 70B идёт первым — он стабильно обрабатывает 11 инструментов и сложные схемы.
# 8B путается когда инструментов больше 8 или схемы сложные.
MODELS = [
    "llama-3.3-70b-versatile", # Primary: надёжный tool calling
    "qwen/qwen3-32b",          # Fallback 1
    "llama-3.1-8b-instant",    # Fallback 2: только если всё остальное упало
]
MODEL = MODELS[0]

HISTORY_LIMIT    = 6
SUMMARY_INTERVAL = 20
