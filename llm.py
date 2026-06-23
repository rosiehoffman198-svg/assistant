import json
import logging
from datetime import datetime

from groq import Groq, AuthenticationError
from config import GROQ_API_KEY, MODELS
from tools import (
    create_task, get_tasks, complete_task,
    save_note, search_notes,
    create_reminder, get_reminders,
    pin_fact, load_pinned_facts, get_profile,
    count_messages_since_last_summary, get_messages_for_summary,
    save_summary, get_latest_summary,
)
from config import SUMMARY_INTERVAL

logger = logging.getLogger(__name__)

client = Groq(api_key=GROQ_API_KEY)


# ─── Model fallback ───────────────────────────────────────────────────────────

def _call_with_fallback(**kwargs) -> object:
    """Try each model in MODELS in order. Skip to the next on any error
    except AuthenticationError (wrong key — no point retrying)."""
    last_err = None
    for model in MODELS:
        try:
            return client.chat.completions.create(model=model, **kwargs)
        except AuthenticationError:
            raise  # same key, same result on every model
        except Exception as e:
            logger.warning("Model %s failed (%s: %s), trying next", model, type(e).__name__, e)
            last_err = e
    raise last_err or RuntimeError("All models in MODELS list exhausted")


# ─── System prompt (kept short on purpose) ───────────────────────────────────

def build_system_prompt() -> str:
    profile = get_profile()
    pinned  = load_pinned_facts()
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    name  = profile.get("name", "—")
    goals = profile.get("goals", "—")
    style = profile.get("style", "кратко и прямо")
    extra = profile.get("extra", "")

    lines = [
        f"Личный ассистент. Сейчас: {now}.",
        f"Пользователь: {name}. Цели: {goals}. Стиль: {style}.",
        "Правила: отвечай кратко, на русском, без воды.",
        "Инструменты используй автоматически — не спрашивай лишний раз.",
        "Новый важный факт о пользователе → сохрани через pin_fact.",
    ]
    if extra:
        lines.append(f"Доп. контекст: {extra}.")
    if pinned:
        lines.append(f"Запомненные факты:\n{pinned}")

    return "\n".join(lines)


# ─── Summary generation (runs in background thread) ──────────────────────────

def maybe_generate_summary():
    """Called after every user message. Generates a summary every
    SUMMARY_INTERVAL messages and stores it in the DB."""
    count = count_messages_since_last_summary()
    if count < SUMMARY_INTERVAL:
        return

    messages = get_messages_for_summary(SUMMARY_INTERVAL)
    if not messages:
        return

    dialogue = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    try:
        # Use a dedicated client instance — this runs in a thread pool
        _client = Groq(api_key=GROQ_API_KEY)
        resp = _client.chat.completions.create(
            model=MODELS[0],  # always use the fastest model for summaries
            messages=[{
                "role": "user",
                "content": (
                    "Сделай краткое summary диалога в 3-4 предложениях. "
                    "Только факты: что обсуждали, что решили, что создали.\n\n"
                    + dialogue
                ),
            }],
            max_tokens=200,
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            save_summary(text, count)
            logger.info("Conversation summary saved (%d messages)", count)
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)


# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Создать задачу. Вызывай когда пользователь упоминает что нужно сделать.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":    {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD или null"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Получить список активных задач.",
            "parameters": {
                "type": "object",
                "properties": {
                    "show_completed": {"type": "boolean"},
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
                "properties": {
                    "task_id": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Сохранить заметку, идею, мысль.",
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
            "description": "Найти заметки по ключевым словам (FTS5).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
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
            "description": "Запомнить важный факт о пользователе навсегда.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                },
                "required": ["content"],
            },
        },
    },
]

TOOL_MAP = {
    "create_task":     create_task,
    "get_tasks":       get_tasks,
    "complete_task":   complete_task,
    "save_note":       save_note,
    "search_notes":    search_notes,
    "create_reminder": create_reminder,
    "get_reminders":   get_reminders,
    "pin_fact":        pin_fact,
}


# ─── Main LLM call with tool loop ─────────────────────────────────────────────

def call_llm(messages: list[dict]) -> str:
    system_prompt  = build_system_prompt()
    latest_summary = get_latest_summary()

    # Context: system → [summary block] → last N messages
    # Summary replaces the long history, keeping the window small.
    context: list[dict] = []
    if latest_summary:
        context.append({"role": "user",      "content": f"[Контекст прошлого диалога]\n{latest_summary}"})
        context.append({"role": "assistant", "content": "Понял, учту."})
    context.extend(messages)

    full_messages = [{"role": "system", "content": system_prompt}] + context

    response = _call_with_fallback(
        messages=full_messages,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
        max_tokens=800,
        temperature=0.7,
    )

    msg = response.choices[0].message

    # No tool calls — return text directly
    if not msg.tool_calls:
        return msg.content or "…"

    # Execute every requested tool
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

    tool_calls_dicts = [
        {
            "id":   tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in msg.tool_calls
    ]

    final_messages = (
        full_messages
        + [{"role": "assistant", "content": msg.content or "", "tool_calls": tool_calls_dicts}]
        + tool_results
    )

    final = _call_with_fallback(
        messages=final_messages,
        max_tokens=500,
        temperature=0.7,
    )
    return final.choices[0].message.content or "Готово."
