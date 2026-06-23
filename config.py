import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MY_TELEGRAM_ID  = int(os.getenv("MY_TELEGRAM_ID", "0"))
DB_PATH         = os.getenv("DB_PATH", "assistant.db")

# Model fallback chain — fastest/cheapest first.
# If a model returns 429/503 or any error, the next one is tried automatically.
# Verify exact names at console.groq.com/docs/models
MODELS = [
    "llama-3.1-8b-instant",    # Primary: fastest, cheapest, free tier friendly
    "qwen/qwen3-32b",          # Fallback 1: smarter when 8B isn't enough
    "llama-3.3-70b-versatile", # Fallback 2: heaviest, use as last resort
]
MODEL = MODELS[0]  # backward-compat alias (used in profile extraction)

HISTORY_LIMIT    = 6   # messages kept in LLM context window
SUMMARY_INTERVAL = 20  # generate a summary every N saved messages
