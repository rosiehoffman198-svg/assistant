"""Контекст текущего запроса.

CURRENT_USER_ID протаскивается в функции tools.py через ContextVar, а не через
параметр функции. Так поле user_id не попадает в LLM-facing сигнатуры инструментов
(_prepare_args всё равно его отбросит) и модель не может его «галлюцинировать».

asyncio.to_thread копирует контекст в рабочий поток, где выполняются все Groq/
psycopg вызовы, поэтому CURRENT_USER_ID доступен и там.
"""

from contextvars import ContextVar

CURRENT_USER_ID: ContextVar[int] = ContextVar("CURRENT_USER_ID")


def current_user_id() -> int:
    """ID пользователя, от которого пришёл текущий запрос.

    Если контекст не задан (фоновая задача/scheduler без запроса) — это баг, и
    лучше упасть громко, чем молча записать строку с NULL user_id.
    """
    try:
        return CURRENT_USER_ID.get()
    except LookupError:
        raise RuntimeError(
            "CURRENT_USER_ID не задан в контексте. process_message() должен "
            "выставить его до вызова любого инструмента."
        )
