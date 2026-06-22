import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MY_TELEGRAM_ID  = int(os.getenv("MY_TELEGRAM_ID", "0"))
DB_PATH         = os.getenv("DB_PATH", "assistant.db")
MODEL           = os.getenv("MODEL", "llama-3.3-70b-versatile")
