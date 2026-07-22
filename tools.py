import logging
import re
from datetime import datetime

from config import TASKS_LIMIT
from database import db

logger = logging.getLogger(__name__)

PRIORITY_ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
ENERGY_ICONS   = {"high": "⚡", "medium": "🔋", "low": "🪫"}
STATUS_ICONS   = {"active": "🟢", "paused": "⏸", "done": "✅"}

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H",
    "%Y-%m-%d",
)


def parse_dt(value) -> datetime | None:
    """Accept the sloppy datetime strings an LLM actually emits.

    strptime tolerates unpadded month/hour, so "2026-7-20 9:05" parses here
    even though it used to sort lexicographically after every other date.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ─── Projects ──────────────────────────────────────────────────────────────────

def create_project(name: str, goal: str = "", status: str = "active") -> str:
    with db() as c:
        c.execute(
            "INSERT INTO projects (name, goal, status) VALUES (%s, %s, %s) RETURNING id",
            (name, goal, status),
        )
        pid = c.fetchone()["id"]
    return f"📁 Проект [{pid}]: {name}\n🎯 Цель: {goal or 'не указана'}"


def get_projects(status_filter: str = None) -> str:
    with db() as c:
        if status_filter:
            c.execute("SELECT * FROM projects WHERE status = %s ORDER BY created_at", (status_filter,))
        else:
            c.execute("SELECT * FROM projects ORDER BY status, created_at")
        rows = c.fetchall()
    if not rows:
        return "📁 Нет проектов"
    lines = ["📁 Проекты:\n"]
    for row in rows:
        icon     = STATUS_ICONS.get(row["status"], "⚪")
        goal_str = f"\n   🎯 {row['goal']}" if row["goal"] else ""
        lines.append(f"{icon} [{row['id']}] {row['name']}{goal_str}")
    return "\n".join(lines)


def update_project(project_id: int, status: str = None, goal: str = None, name: str = None) -> str:
    updates, values = [], []
    if status is not None: updates.append("status = %s"); values.append(status)
    if goal   is not None: updates.append("goal = %s");   values.append(goal)
    if name   is not None: updates.append("name = %s");   values.append(name)
    if not updates:
        return "❌ Нечего обновлять"
    values.append(project_id)
    with db() as c:
        c.execute(f"UPDATE projects SET {', '.join(updates)} WHERE id = %s RETURNING name", values)
        row = c.fetchone()
    return f"✅ Проект [{project_id}] обновлён" if row else f"❌ Проект [{project_id}] не найден"


def get_active_projects_summary() -> str:
    """One-liner per project for system prompt."""
    with db() as c:
        c.execute("""
            SELECT p.id, p.name, p.goal,
                   COUNT(t.id) FILTER (WHERE t.completed = 0) AS open_tasks
            FROM projects p
            LEFT JOIN tasks t ON t.project_id = p.id
            WHERE p.status = 'active'
            GROUP BY p.id ORDER BY p.created_at LIMIT 25
        """)
        rows = c.fetchall()
    if not rows:
        return ""
    lines = []
    for r in rows:
        goal_str = f" | цель: {r['goal']}" if r["goal"] else ""
        lines.append(f"• [{r['id']}] {r['name']} — {r['open_tasks']} задач{goal_str}")
    return "\n".join(lines)


# ─── Tasks ─────────────────────────────────────────────────────────────────────

def create_task(
    title: str,
    priority: str = "medium",
    importance: str = "medium",
    energy: str = "medium",
    deadline: str = None,
    project_id: int = None,
) -> str:
    warning = ""
    if project_id is not None:
        # The FK would raise; checking first lets us save the task and say why.
        with db() as c:
            c.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
            if c.fetchone() is None:
                warning    = f"\n⚠️ Проект [{project_id}] не найден — задача создана без проекта."
                project_id = None

    with db() as c:
        c.execute(
            """INSERT INTO tasks (title, priority, importance, energy, deadline, project_id)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (title, priority, importance, energy, deadline, project_id),
        )
        task_id = c.fetchone()["id"]

    p_icon = PRIORITY_ICONS.get(priority, "⚪")
    e_icon = ENERGY_ICONS.get(energy, "")
    dl_str = f" (до {deadline})" if deadline else ""
    pr_str = f" [проект {project_id}]" if project_id else ""
    return f"✅ [{task_id}] {p_icon}{e_icon} {title}{dl_str}{pr_str}{warning}"


def get_tasks(
    show_completed: bool = False,
    energy: str = None,
    project_id: int = None,
) -> str:
    conditions = ["t.completed = %s"]
    values     = [1 if show_completed else 0]
    if energy:
        conditions.append("t.energy = %s")
        values.append(energy)
    if project_id is not None:
        conditions.append("t.project_id = %s")
        values.append(project_id)
    where = " AND ".join(conditions)
    values.append(TASKS_LIMIT + 1)  # +1 row tells us the list was truncated

    with db() as c:
        c.execute(f"""
            SELECT t.*, p.name AS project_name
            FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
            WHERE {where}
            ORDER BY CASE t.priority   WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     CASE t.importance WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     t.created_at
            LIMIT %s
        """, values)
        rows = c.fetchall()

    if not rows:
        return "📋 Нет задач"
    truncated = len(rows) > TASKS_LIMIT
    rows      = rows[:TASKS_LIMIT]

    lines = ["📋 Задачи:\n"]
    for row in rows:
        p_icon = PRIORITY_ICONS.get(row["priority"], "⚪")
        e_icon = ENERGY_ICONS.get(row["energy"], "")
        dl     = f" — до {row['deadline']}" if row["deadline"] else ""
        proj   = f" [{row['project_name']}]" if row["project_name"] else ""
        lines.append(f"{p_icon}{e_icon} [{row['id']}] {row['title']}{dl}{proj}")
    if truncated:
        lines.append(f"\n… показаны первые {TASKS_LIMIT}. Уточни фильтр или закрой часть задач.")
    return "\n".join(lines)


def complete_task(task_id: int) -> str:
    with db() as c:
        c.execute("UPDATE tasks SET completed = 1, completed_at = NOW() WHERE id = %s", (task_id,))
        affected = c.rowcount
    return f"✅ Задача [{task_id}] выполнена!" if affected else f"❌ Задача [{task_id}] не найдена"


# ─── Notes ─────────────────────────────────────────────────────────────────────

def save_note(content: str, tags: str = "") -> str:
    with db() as c:
        c.execute("INSERT INTO notes (content, tags) VALUES (%s, %s) RETURNING id", (content, tags))
        note_id = c.fetchone()["id"]
    tags_str = f" #{tags.replace(',', ' #')}" if tags else ""
    return f"📝 Заметка [{note_id}]{tags_str}"


def search_notes(query: str) -> str:
    # 'russian' stems, so «молока» matches a note saying «молоко», and stopwords
    # like «как» stop being mandatory AND-terms.
    with db() as c:
        c.execute("""
            SELECT id, content, tags, created_at,
                   ts_rank(
                       to_tsvector('russian', content || ' ' || COALESCE(tags, '')),
                       plainto_tsquery('russian', %s)
                   ) AS rank
            FROM notes
            WHERE to_tsvector('russian', content || ' ' || COALESCE(tags, ''))
                  @@ plainto_tsquery('russian', %s)
            ORDER BY rank DESC, created_at DESC LIMIT 5
        """, (query, query))
        rows = c.fetchall()

    if not rows:
        return f"🔍 Ничего по запросу: «{query}»"
    lines = [f"🔍 Найдено {len(rows)}:\n"]
    for row in rows:
        preview  = row["content"][:120] + ("…" if len(row["content"]) > 120 else "")
        tags_str = f" #{row['tags']}" if row["tags"] else ""
        lines.append(f"[{row['id']}] {preview}\n   {str(row['created_at'])[:10]}{tags_str}\n")
    return "\n".join(lines)


# ─── Reminders ─────────────────────────────────────────────────────────────────

CANONICAL_DT = "%Y-%m-%d %H:%M"
# Fixed-width zero-padded timestamps sort lexicographically exactly as they sort
# chronologically, which is what makes the TEXT column safe to compare with <=.
CANONICAL_RE = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"


def create_reminder(title: str, remind_at: str) -> str:
    when = parse_dt(remind_at)
    if when is None:
        return (
            f"❌ Не понял время «{remind_at}». "
            "Нужен формат YYYY-MM-DD HH:MM, например 2026-07-21 09:00."
        )
    stamp = when.strftime(CANONICAL_DT)  # normalise before storing, never raw LLM text
    with db() as c:
        c.execute(
            "INSERT INTO reminders (title, remind_at) VALUES (%s, %s) RETURNING id",
            (title, stamp),
        )
        rid = c.fetchone()["id"]
    return f"⏰ [{rid}] {title} — {stamp}"


def get_reminders() -> str:
    with db() as c:
        c.execute("SELECT * FROM reminders WHERE sent = 0 ORDER BY remind_at LIMIT 10")
        rows = c.fetchall()
    if not rows:
        return "⏰ Нет напоминаний"
    lines = ["⏰ Напоминания:\n"]
    for row in rows:
        stamp = row["remind_at"]
        # Rows written before normalisation existed can't be compared reliably;
        # flag them rather than pretending they will fire.
        broken = "" if re.match(CANONICAL_RE, str(stamp)) else "  ⚠️ формат не распознан"
        lines.append(f"[{row['id']}] {row['title']}\n   🕐 {stamp}{broken}\n")
    return "\n".join(lines)


def get_due_reminders(now: datetime) -> list:
    stamp = now.strftime(CANONICAL_DT)
    with db() as c:
        c.execute(
            "SELECT * FROM reminders "
            "WHERE sent = 0 AND remind_at ~ %s AND remind_at <= %s",
            (CANONICAL_RE, stamp),
        )
        return c.fetchall()


def mark_reminder_sent(reminder_id: int):
    with db() as c:
        c.execute("UPDATE reminders SET sent = 1 WHERE id = %s", (reminder_id,))


# ─── Pinned facts ──────────────────────────────────────────────────────────────

def pin_fact(content: str) -> str:
    with db() as c:
        c.execute("INSERT INTO pinned_facts (content) VALUES (%s)", (content,))
    return f"📌 Запомнил: {content}"


def load_pinned_facts() -> str:
    with db() as c:
        c.execute("SELECT content FROM pinned_facts ORDER BY created_at DESC LIMIT 20")
        rows = c.fetchall()
    return "\n".join(f"• {r['content']}" for r in rows) if rows else ""


def clear_pinned_facts() -> int:
    with db() as c:
        c.execute("DELETE FROM pinned_facts")
        return c.rowcount


# ─── Profile ───────────────────────────────────────────────────────────────────

PROFILE_KEYS = ("name", "goals", "style", "extra")


def get_profile(include_internal: bool = False) -> dict:
    with db() as c:
        c.execute("SELECT key, value FROM profile")
        rows = c.fetchall()
    return {
        r["key"]: r["value"]
        for r in rows
        if include_internal or not r["key"].startswith("_")
    }


def set_profile(key: str, value) -> bool:
    """Only whitelisted keys, only scalars — a list from the LLM used to raise
    mid-loop and discard the rest of the extracted profile."""
    if not key.startswith("_") and key not in PROFILE_KEYS:
        logger.warning(f"Profile: ignoring unknown key {key!r}")
        return False
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    elif not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        return False
    with db() as c:
        c.execute(
            "INSERT INTO profile (key, value, updated_at) VALUES (%s, %s, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            (key, value),
        )
    return True


# ─── Message history ───────────────────────────────────────────────────────────

def get_last_messages(n: int = 6) -> list[dict]:
    with db() as c:
        c.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT %s", (n,))
        rows = c.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_message(role: str, content: str):
    with db() as c:
        c.execute("INSERT INTO messages (role, content) VALUES (%s, %s)", (role, content))


def clear_history() -> str:
    """/reset must also drop summaries — otherwise the 'deleted' conversation
    kept resurfacing through the system prompt."""
    with db() as c:
        c.execute("DELETE FROM messages")
        messages = c.rowcount
        c.execute("DELETE FROM conversation_summaries")
        summaries = c.rowcount
    return f"🗑 Очищено: {messages} сообщений, {summaries} сводок.\n📌 Закреплённые факты сохранены (/forget — удалить их)."


# ─── Summaries ─────────────────────────────────────────────────────────────────

def get_summary_state() -> tuple[int, str]:
    """(last summarized message id, previous summary text)."""
    with db() as c:
        c.execute(
            "SELECT content, last_message_id FROM conversation_summaries ORDER BY id DESC LIMIT 1"
        )
        row = c.fetchone()
    if not row:
        return 0, ""
    return row["last_message_id"] or 0, row["content"]


def count_messages_since(last_message_id: int) -> int:
    with db() as c:
        c.execute("SELECT COUNT(*) AS count FROM messages WHERE id > %s", (last_message_id,))
        return c.fetchone()["count"]


def get_messages_after(last_message_id: int, limit: int = 20) -> list[dict]:
    with db() as c:
        c.execute(
            "SELECT id, role, content FROM messages WHERE id > %s ORDER BY id LIMIT %s",
            (last_message_id, limit),
        )
        rows = c.fetchall()
    return [{"id": r["id"], "role": r["role"], "content": r["content"]} for r in rows]


def save_summary(content: str, last_message_id: int, messages_count: int):
    with db() as c:
        c.execute(
            "INSERT INTO conversation_summaries (content, last_message_id, messages_count) "
            "VALUES (%s, %s, %s)",
            (content, last_message_id, messages_count),
        )


def get_latest_summary() -> str:
    with db() as c:
        c.execute("SELECT content FROM conversation_summaries ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
    return row["content"] if row else ""
