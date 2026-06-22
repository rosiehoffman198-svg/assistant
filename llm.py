import json
from datetime import datetime
from groq import Groq
from config import GROQ_API_KEY, MODEL
from tools import (
    create_task, get_tasks, complete_task,
    save_note, search_notes,
    create_reminder, get_reminders,
    pin_fact, load_pinned_facts, get_profile,
)

client = Groq(api_key=GROQ_API_KEY)

# ─── Tool definitions (Groq/OpenAI format) ────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Создать задачу. Вызывай когда пользователь упоминает что нужно сделать.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":    {"type": "string", "description": "Название задачи"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "deadline": {"type": "string", "description": "Дедлайн YYYY-MM-DD или null"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Получить список активных задач пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "show_completed": {"type": "boolean", "description": "true — показать выполненные"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Отметить задачу как выполненную по ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Сохранить заметку, идею, мысль. Вызывай когда пользователь хочет что-то записать.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Содержание заметки"},
                    "tags":    {"type": "string", "description": "Теги через запятую, например: работа,идея"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Найти заметки по ключевым словам через FTS5 поиск.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"},
                },
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
                    "title":     {"type": "string", "description": "Текст напоминания"},
                    "remind_at": {"type": "string", "description": "Время в формате YYYY-MM-DD HH:MM"},
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
                "properties": {
                    "content": {"type": "string", "description": "Важный факт"},
                },
                "required": ["content"],
            },
        },
    },
]

TOOL_MAP = {
    "create_task":    create_task,
    "get_tasks":      get_tasks,
    "complete_task":  complete_task,
    "save_note":      save_note,
    "search_notes":   search_notes,
    "create_reminder": create_reminder,
    "get_reminders":  get_reminders,
    "pin_fact":       pin_fact,
}


# ─── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    profile = get_profile()
    pinned  = load_pinned_facts()
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    name   = profile.get("name", "не указано")
    goals  = profile.get("goals", "не указаны")
    style  = profile.get("style", "краткий и прямой")
    extra  = profile.get("extra", "")

    return f"""Ты — личный AI-ассистент.

Сейчас: {now}

## Профиль пользователя
- Имя: {name}
- Цели: {goals}
- Стиль общения: {style}
{('- Дополнительно: ' + extra) if extra else ''}

## Запомненные факты
{pinned if pinned else '(пока нет)'}

## Правила
- Отвечаешь кратко и по делу, без воды
- Автоматически создаёшь задачи/заметки/напоминания через инструменты — не спрашивай лишний раз
- Если видишь новый важный факт о пользователе — сохраняй через pin_fact
- Всегда отвечаешь на русском
- Никакой мотивашки и «ты справишься» — только конкретные действия
"""


# ─── Main LLM call with tool loop ─────────────────────────────────────────────

def call_llm(messages: list[dict]) -> str:
    system_prompt = build_system_prompt()
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    response = client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
        max_tokens=1000,
        temperature=0.7,
    )

    msg = response.choices[0].message

    # No tools needed — return text directly
    if not msg.tool_calls:
        return msg.content or "…"

    # Execute all tool calls
    tool_results = []
    for tc in msg.tool_calls:
        func_name = tc.function.name
        try:
            func_args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            func_args = {}

        tool_func = TOOL_MAP.get(func_name)
        result = tool_func(**func_args) if tool_func else f"Инструмент {func_name} не найден"

        tool_results.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "content":      result,
        })

    # Build assistant message with tool_calls (dict format for API)
    tool_calls_dicts = [
        {
            "id":   tc.id,
            "type": "function",
            "function": {
                "name":      tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        for tc in msg.tool_calls
    ]

    # Second call: model summarises tool results
    final_messages = (
        full_messages
        + [{"role": "assistant", "content": msg.content or "", "tool_calls": tool_calls_dicts}]
        + tool_results
    )

    final = client.chat.completions.create(
        model=MODEL,
        messages=final_messages,
        max_tokens=500,
        temperature=0.7,
    )
    return final.choices[0].message.content or "Готово."
