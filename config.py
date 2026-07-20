import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
DATABASE_URL   = os.getenv("DATABASE_URL", "")


def _read_owner_id() -> int:
    """Fail loudly on a missing/blank/garbage MY_TELEGRAM_ID instead of ignoring every message."""
    raw = os.getenv("MY_TELEGRAM_ID", "").strip()
    if not raw:
        raise RuntimeError(
            "MY_TELEGRAM_ID не задан. Без него бот проигнорирует все сообщения. "
            "Узнать свой ID можно у @userinfobot."
        )
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"MY_TELEGRAM_ID должен быть числом, получено: {raw!r}")


MY_TELEGRAM_ID = _read_owner_id()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не задан.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY не задан.")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан. Нужна строка подключения к PostgreSQL, "
        "например postgresql://user:pass@host:5432/dbname"
    )

# Единая таймзона для всего приложения: наивные datetime в коде, сессия БД и
# промпт модели используют её же, поэтому времена везде сопоставимы.
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
try:
    TZ = ZoneInfo(TIMEZONE)
except Exception as e:
    raise RuntimeError(f"Некорректная TIMEZONE={TIMEZONE!r}: {e}")


def now_local() -> datetime:
    """Naive datetime in TIMEZONE — matches what the DB session writes."""
    return datetime.now(TZ).replace(tzinfo=None)


# groq/compound удалён — не поддерживает tool calling
MODELS = [
    "llama-3.3-70b-versatile",  # Primary: надёжный tool calling
    "llama-3.1-8b-instant",     # Fallback, он же быстрый для служебных задач
]
FAST_MODEL = MODELS[-1]

WHISPER_MODEL    = "whisper-large-v3-turbo"
HISTORY_LIMIT    = 6
SUMMARY_INTERVAL = 20

DB_POOL_MAX      = int(os.getenv("DB_POOL_MAX", "5"))
TASKS_LIMIT      = 50    # верхняя граница выдачи /tasks (читаемость и токены, не лимит Telegram)
TELEGRAM_MAX_LEN = 4000  # с запасом к лимиту обычного sendMessage в 4096

# Rich Messages (Bot API 10.1, sendRichMessage). Лимит взят из стороннего
# описания спецификации — на core.telegram.org подтвердить не удалось, страница
# отдаётся усечённой. Поэтому это порог для попытки, а не гарантия: при отказе
# API код откатывается на обычную отправку с разбивкой.
RICH_MAX_LEN     = 32768
