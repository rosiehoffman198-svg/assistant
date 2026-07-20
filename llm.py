import inspect
import json
import logging
import threading

from groq import Groq

from config import GROQ_API_KEY, MODELS, SUMMARY_INTERVAL, now_local
from tools import (
    create_task, get_tasks, complete_task,
    save_note, search_notes,
    create_reminder, get_reminders,
    pin_fact, load_pinned_facts, get_profile,
    create_project, get_projects, update_project, get_active_projects_summary,
    get_summary_state, count_messages_since, get_messages_after,
    save_summary, get_latest_summary,
)

logger = logging.getLogger(__name__)
client = Groq(api_key=GROQ_API_KEY)

# anyOf + null разрешает модели передавать null для опциональных полей.
# Без этого Groq отклоняет вызов с 400 tool_use_failed.
_nullable_str = lambda desc: {"anyOf": [{"type": "string"}, {"type": "null"}], "description": desc}
_nullable_int = lambda desc: {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": desc}

LEVELS = ["high", "medium", "low"]

# ─── Tool definitions ──────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Создать задачу. Вызывай ТОЛЬКО если пользователь явно говорит о деле которое нужно сделать. "
                "НЕ создавай задачи для приветствий, вопросов или разговоров."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title":      {"type": "string", "description": "Название задачи"},
                    "priority":   {"type": "string", "enum": LEVELS,
                                   "description": "Приоритет. Используй 'medium' если не ясно."},
                    "importance": {"type": "string", "enum": LEVELS,
                                   "description": "Важность для целей. Используй 'medium' если не ясно."},
                    "energy":     {"type": "string", "enum": LEVELS,
                                   "description": "Энергозатратность. Используй 'medium' если не ясно."},
                    "deadline":   _nullable_str("Дедлайн YYYY-MM-DD. null если нет."),
                    "project_id": _nullable_int("ID существующего проекта. null если нет."),
                },
                "required": ["title", "priority", "importance", "energy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Получить список задач пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "show_completed": {"type": "boolean", "description": "true чтобы показать выполненные"},
                    "energy":        {"type": "string", "enum": LEVELS},
                    "project_id":    _nullable_int("Фильтр по проекту"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Отметить задачу выполненной по ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "Создать проект. Вызывай только если пользователь явно говорит о новом проекте.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string"},
                    "goal":   {"type": "string", "description": "Измеримая цель проекта"},
                    "status": {"type": "string", "enum": ["active", "paused", "done"]},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_projects",
            "description": "Показать список проектов.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {"type": "string", "enum": ["active", "paused", "done"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_project",
            "description": "Обновить статус, цель или название проекта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "name":       _nullable_str("Новое название"),
                    "status":     {"type": "string", "enum": ["active", "paused", "done"]},
                    "goal":       _nullable_str("Новая цель"),
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Сохранить заметку или идею. Только когда пользователь хочет что-то записать.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "tags":    {"type": "string", "description": "Теги через запятую"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Найти заметки по ключевым словам.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Создать напоминание на конкретное время.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":     {"type": "string"},
                    "remind_at": {"type": "string",
                                  "description": "Строго YYYY-MM-DD HH:MM с ведущими нулями, например 2026-07-21 09:00"},
                },
                "required": ["title", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reminders",
            "description": "Показать активные напоминания.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_fact",
            "description": "Запомнить важный факт о пользователе навсегда (цели, предпочтения, контекст).",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
]

TOOL_MAP = {
    "create_task":     create_task,
    "get_tasks":       get_tasks,
    "complete_task":   complete_task,
    "create_project":  create_project,
    "get_projects":    get_projects,
    "update_project":  update_project,
    "save_note":       save_note,
    "search_notes":    search_notes,
    "create_reminder": create_reminder,
    "get_reminders":   get_reminders,
    "pin_fact":        pin_fact,
}


# ─── Tool argument handling ────────────────────────────────────────────────────

def _to_bool(value) -> bool:
    # bool("false") is True — the 8B model emits stringified booleans often
    # enough that this silently inverted the task list.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "да")
    return bool(value)


def _coerce(value, annotation):
    if annotation is bool:
        return _to_bool(value)
    if annotation is int:
        return int(str(value).strip())
    if annotation is str:
        return value if isinstance(value, str) else str(value)
    return value


def _prepare_args(func, raw: dict) -> tuple[dict, str | None]:
    """Filter LLM-supplied kwargs against the real signature and coerce types.

    Previously these were splatted straight into the function, so one
    hallucinated key raised TypeError and destroyed the whole turn.
    """
    params = inspect.signature(func).parameters
    clean  = {}
    for key, value in raw.items():
        if key not in params:
            logger.info(f"{func.__name__}: dropping unknown arg {key!r}")
            continue
        if value is None:
            continue  # let the Python default apply
        try:
            clean[key] = _coerce(value, params[key].annotation)
        except (TypeError, ValueError):
            logger.info(f"{func.__name__}: bad value for {key!r}: {value!r}")
            return {}, f"❌ Неверное значение для «{key}»: {value!r}"

    missing = [
        name for name, p in params.items()
        if p.default is inspect.Parameter.empty and name not in clean
    ]
    if missing:
        return {}, f"❌ Не хватает обязательных полей: {', '.join(missing)}"
    return clean, None


def _run_tool(name: str, raw_args: str) -> str:
    func = TOOL_MAP.get(name)
    if func is None:
        return f"❌ Инструмент {name} не найден"
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    kwargs, error = _prepare_args(func, parsed)
    if error:
        return error
    try:
        return func(**kwargs)
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return f"❌ Ошибка в «{name}»: {e}"


# ─── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    profile  = get_profile()
    pinned   = load_pinned_facts()
    projects = get_active_projects_summary()
    summary  = get_latest_summary()
    now      = now_local().strftime("%Y-%m-%d %H:%M")

    name  = profile.get("name",  "не указано")
    goals = profile.get("goals", "не указаны")
    style = profile.get("style", "краткий и прямой")
    extra = profile.get("extra", "")

    return f"""Ты — личный AI-ассистент.

Сейчас: {now}

## Профиль
- Имя: {name}
- Цели: {goals}
- Стиль: {style}
{('- ' + extra) if extra else ''}

## Активные проекты
{projects if projects else '(нет)'}

## Запомненные факты
{pinned if pinned else '(нет)'}

## Контекст прошлых разговоров
{summary if summary else '(нет)'}

## Правила
- Отвечаешь кратко и по делу, без воды
- Инструменты используй ТОЛЬКО по явному запросу: задачи — если пользователь говорит "сделать/добавить/напомни", заметки — если говорит "запиши/сохрани"
- На приветствия, вопросы и разговоры — просто отвечай текстом, никаких инструментов
- При создании задачи: всегда указывай priority, importance, energy (не оставляй null)
- Время для напоминаний — всегда YYYY-MM-DD HH:MM с ведущими нулями
- Привязывай задачу к проекту только если этот ID есть в списке активных проектов выше
- Важные факты о пользователе сохраняй через pin_fact
- Отвечай на русском
- Никакой мотивации ради мотивации — только конкретные действия
"""


# ─── Main LLM call ─────────────────────────────────────────────────────────────

def _complete(messages: list[dict], models: list[str], **kwargs):
    """Try each model in order; return (response, model_that_worked)."""
    last_error = None
    for model in models:
        try:
            response = client.chat.completions.create(model=model, messages=messages, **kwargs)
            return response, model
        except Exception as e:
            last_error = e
            logger.warning(f"Model {model} failed: {e}")
    raise RuntimeError(f"Все модели недоступны: {last_error}")


def call_llm(messages: list[dict]) -> str:
    system_prompt = build_system_prompt()
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    try:
        response, used_model = _complete(
            full_messages, MODELS,
            tools=TOOL_DEFINITIONS, tool_choice="auto",
            max_tokens=1000, temperature=0.7,
        )
    except RuntimeError as e:
        logger.error(str(e))
        return "❌ Все модели недоступны. Попробуй позже."

    msg = response.choices[0].message
    if not msg.tool_calls:
        return msg.content or "…"

    tool_results, summaries = [], []
    for tc in msg.tool_calls:
        result = _run_tool(tc.function.name, tc.function.arguments)
        summaries.append(result)
        tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    tool_calls_dicts = [
        {"id": tc.id, "type": "function",
         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        for tc in msg.tool_calls
    ]

    # Keep the model that just worked at the head of the list: the tools have
    # already committed to the DB, so failing here would make the user retry and
    # duplicate the write.
    ordered = [used_model] + [m for m in MODELS if m != used_model]
    try:
        final, _ = _complete(
            full_messages
            + [{"role": "assistant", "content": msg.content or "", "tool_calls": tool_calls_dicts}]
            + tool_results,
            ordered,
            tools=TOOL_DEFINITIONS, tool_choice="none",
            max_tokens=500, temperature=0.7,
        )
    except RuntimeError as e:
        # Report what actually happened rather than a generic error — the work
        # is already done and must not be repeated.
        logger.error(f"Follow-up call failed: {e}")
        return "\n".join(summaries)

    return final.choices[0].message.content or "\n".join(summaries) or "Готово."


# ─── Summary generation ────────────────────────────────────────────────────────

_summary_lock = threading.Lock()


def maybe_generate_summary():
    """Runs in a worker thread; must never raise into an unawaited task."""
    if not _summary_lock.acquire(blocking=False):
        return  # another summary is already in flight
    try:
        last_id, previous = get_summary_state()
        count = count_messages_since(last_id)
        if count < SUMMARY_INTERVAL:
            return

        messages = get_messages_after(last_id, limit=SUMMARY_INTERVAL)
        if not messages:
            return
        new_last_id = messages[-1]["id"]

        # Feed the previous summary back in — otherwise every summary replaced
        # the last one and older facts were lost permanently.
        previous_block = f"Что уже известно:\n{previous}\n\n" if previous else ""
        prompt = (
            "Обнови сводку о пользователе: объедини уже известное с новым диалогом. "
            "Сохрани важные старые факты, добавь новые. 5-10 буллет-поинтов на русском, "
            "только факты, без воды.\n\n"
            + previous_block
            + "Новый диалог:\n"
            + "\n".join(f"{m['role']}: {m['content'][:200]}" for m in messages)
        )

        resp, _ = _complete(
            [{"role": "user", "content": prompt}], MODELS,
            max_tokens=400, temperature=0.3,
        )
        summary = (resp.choices[0].message.content or "").strip()
        if summary:
            save_summary(summary, new_last_id, len(messages))
            logger.info(f"Summary saved up to message {new_last_id}")
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
    finally:
        _summary_lock.release()
