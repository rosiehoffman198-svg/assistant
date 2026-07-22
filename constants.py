"""Единый источник правды для enum-значений.

Каждый класс хранит допустимые значения для конкретного поля. Эти же значения
фигурируют в CHECK-ограничениях таблиц (database.py) — держим их в синхроне
через модуль, а не через копипасту строк, чтобы не получить «Active»/«ACTIVE»/
«finished» в разных регистрах и вариантах.

Принятые строковые значения (нижний регистр, латиница) совпадают с тем, что
ожидает LLM в enum-параметрах инструментов.
"""

# ─── Общие ────────────────────────────────────────────────────────────────────

# Приоритет / важность / энергозатратность — один набор для всех модулей.
LEVELS = ("high", "medium", "low")


class Priority:
    """Приоритет задачи/цели."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ─── Статусы сущностей ─────────────────────────────────────────────────────────

class GoalStatus:
    ACTIVE = "active"
    DONE = "done"
    PAUSED = "paused"

    ALL = (ACTIVE, DONE, PAUSED)


class ProjectStatus:
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"

    ALL = (ACTIVE, PAUSED, DONE)


class InboxStatus:
    """Жизненный цикл записи в Inbox: новая → распределена → завершена."""
    INBOX = "inbox"       # не обработана
    TASK = "task"         # превращена в задачу
    NOTE = "note"         # превращена в заметку
    EXPENSE = "expense"   # превращена в расход
    HEALTH = "health"     # превращена в запись здоровья
    DONE = "done"         # закрыта как нерелевантная

    ALL = (INBOX, TASK, NOTE, EXPENSE, HEALTH, DONE)


class InboxType:
    """Источник записи в Inbox."""
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    FORWARD = "forward"

    ALL = (TEXT, VOICE, IMAGE, DOCUMENT, FORWARD)


class TransactionType:
    INCOME = "income"
    EXPENSE = "expense"

    ALL = (INCOME, EXPENSE)


# ─── Финансы ──────────────────────────────────────────────────────────────────

class Account:
    """Счёт/кошелёк транзакции."""
    CASH = "cash"
    CARD = "card"
    HUMO = "humo"
    VISA = "visa"
    CRYPTO = "crypto"

    ALL = (CASH, CARD, HUMO, VISA, CRYPTO)


DEFAULT_CURRENCY = "UZS"


# ─── Здоровье ──────────────────────────────────────────────────────────────────

class Mood:
    GOOD = "good"
    NORMAL = "normal"
    BAD = "bad"

    ALL = (GOOD, NORMAL, BAD)


class Energy:
    """Самочувствие/энергия в записи здоровья (≠ энергозатратность задачи)."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    ALL = (HIGH, MEDIUM, LOW)


class Stress:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    ALL = (HIGH, MEDIUM, LOW)
