import json
import logging
from datetime import datetime
from groq import Groq
from config import GROQ_API_KEY, MODELS, SUMMARY_INTERVAL
from tools import (
    create_task, get_tasks, complete_task,
    save_note, search_notes,
    create_reminder, get_reminders,
    pin_fact, load_pinned_facts, get_profile,
    create_project, get_projects, update_project, get_active_projects_summary,
    count_messages_since_last_summary, get_messages_for_summary,
    save_summary, get_latest_summary,
)

logger = logging.getLogger(__name__)
client = Groq(api_key=GROQ_API_KEY)

# anyOf + null разрешает модели передавать null для опциональных полей.
# Без этого Groq отклоняет вызов с 400 tool_use_failed.
_nullable_str = lambda desc: {"anyOf": [{"type": "string"}, {"type": "null"}], "description": desc}
_nullable_int = lambda desc: {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": desc}

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
                    "priority":   {"type": "string", "enum": ["high", "medium", "low"],
                                   "description": "Приоритет. Используй 'medium' если не ясно."},
                    "importance": {"type": "string", "enum": ["high", "medium", "low"],
                                   "description": "Важность для целей. Используй 'medium' если не ясно."},
                    "energy":     {"type": "string", "enum": ["high", "low"],
                                   "description": "Энергозатратность задачи."},
                    "deadline":   _nullable_str("Дедлайн YYYY-MM-DD. null если нет."),
                    "project_id": _nullable_int("ID проекта. null если нет."),
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
                    "energy":        {"type": "string", "enum": ["high", "low"]},
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
                    "remind_at": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
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
    "create_task":    create_task,
    "get_tasks":      get_tasks,
    "complete_task":  complete_task,
    "create_project": create_project,
    "get_projects":   get_projects,
    "update_project": update_project,
    "save_note":      save_note,
    "search_notes":   search_notes,
    "create_reminder": create_reminder,
    "get_reminders":  get_reminders,
    "pin_fact":       pin_fact,
}


# ─── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    profile  = get_profile()
    pinned   = load_pinned_facts()
    projects = get_active_projects_summary()
    summary  = get_latest_summary()
    now      = datetime.now().strftime("%Y-%m-%d %H:%M")

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
- Если задача явно относится к проекту — привязывай через project_id
- Важные факты о пользователе сохраняй через pin_fact
- Отвечай на русском
- Никакой мотивации ради мотивации — только конкретные действия
"""


# ─── Main LLM call ─────────────────────────────────────────────────────────────

def call_llm(messages: list[dict]) -> str:
    system_prompt = build_system_prompt()
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    response = None
    for model in MODELS:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=full_messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                max_tokens=1000,
                temperature=0.7,
            )
            break
        except Exception as e:
            logger.warning(f"Model {model} failed: {e}")

    if response is None:
        return "❌ Все модели недоступны. Попробуй позже."

    msg = response.choices[0].message
    if not msg.tool_calls:
        return msg.content or "…"

    # Execute tools
    tool_results = []
    for tc in msg.tool_calls:
        try:
            func_args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            func_args = {}
        # Clean up null values — use defaults instead
        func_args = {k: v for k, v in func_args.items() if v is not None}
        tool_func = TOOL_MAP.get(tc.function.name)
        result    = tool_func(**func_args) if tool_func else f"Инструмент {tc.function.name} не найден"
        tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    tool_calls_dicts = [
        {"id": tc.id, "type": "function",
         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        for tc in msg.tool_calls
    ]

    final = client.chat.completions.create(
        model=MODELS[0],
        messages=(
            full_messages
            + [{"role": "assistant", "content": msg.content or "", "tool_calls": tool_calls_dicts}]
            + tool_results
        ),
        max_tokens=500,
        temperature=0.7,
    )
    return final.choices[0].message.content or "Готово."


# ─── Summary generation ────────────────────────────────────────────────────────

def maybe_generate_summary():
    count = count_messages_since_last_summary()
    if count < SUMMARY_INTERVAL:
        return
    messages = get_messages_for_summary(limit=SUMMARY_INTERVAL)
    if not messages:
        return
    prompt = (
        "Сожми в 5-7 ключевых фактов о пользователе, его задачах и работе. "
        "Только факты, без воды. Буллет-поинты на русском.\n\n"
        + "\n".join(f"{m['role']}: {m['content'][:200]}" for m in messages)
    )
    try:
        resp = client.chat.completions.create(
            model=MODELS[0],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        summary = resp.choices[0].message.content or ""
        if summary:
            save_summary(summary, count)
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
