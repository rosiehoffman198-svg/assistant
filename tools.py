from datetime import datetime, timedelta
from database import get_conn

PRIORITY_ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
ENERGY_ICONS   = {"high": "⚡", "medium": "🔋", "low": "🪫"}
STATUS_ICONS   = {"active": "🟢", "paused": "⏸", "done": "✅"}


# ─── Projects ──────────────────────────────────────────────────────────────────

def create_project(name: str, goal: str = "", status: str = "active") -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO projects (name, goal, status) VALUES (%s, %s, %s) RETURNING id",
        (name, goal, status),
    )
    pid = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return f"📁 Проект [{pid}]: {name}\n🎯 Цель: {goal or 'не указана'}"


def get_projects(status_filter: str = None) -> str:
    conn = get_conn()
    c = conn.cursor()
    if status_filter:
        c.execute("SELECT * FROM projects WHERE status = %s ORDER BY created_at", (status_filter,))
    else:
        c.execute("SELECT * FROM projects ORDER BY status, created_at")
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "📁 Нет проектов"
    lines = ["📁 Проекты:\n"]
    for row in rows:
        icon     = STATUS_ICONS.get(row["status"], "⚪")
        goal_str = f"\n   🎯 {row['goal']}" if row["goal"] else ""
        lines.append(f"{icon} [{row['id']}] {row['name']}{goal_str}")
    return "\n".join(lines)


def update_project(project_id: int, status: str = None, goal: str = None, name: str = None) -> str:
    conn = get_conn()
    c = conn.cursor()
    updates, values = [], []
    if status is not None: updates.append("status = %s"); values.append(status)
    if goal   is not None: updates.append("goal = %s");   values.append(goal)
    if name   is not None: updates.append("name = %s");   values.append(name)
    if not updates:
        conn.close()
        return "❌ Нечего обновлять"
    values.append(project_id)
    c.execute(f"UPDATE projects SET {', '.join(updates)} WHERE id = %s RETURNING name", values)
    row = c.fetchone()
    conn.commit()
    conn.close()
    return f"✅ Проект [{project_id}] обновлён" if row else f"❌ Проект [{project_id}] не найден"


def get_active_projects_summary() -> str:
    """One-liner per project for system prompt."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.name, p.goal,
               COUNT(t.id) FILTER (WHERE t.completed = 0) AS open_tasks
        FROM projects p
        LEFT JOIN tasks t ON t.project_id = p.id
        WHERE p.status = 'active'
        GROUP BY p.id ORDER BY p.created_at LIMIT 5
    """)
    rows = c.fetchall()
    conn.close()
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
    # LLM sometimes sends the string "null" instead of JSON null — normalise here
    if deadline in ("null", "None", "", "undefined"):
        deadline = None
    if not isinstance(project_id, int):
        project_id = None
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO tasks (title, priority, importance, energy, deadline, project_id)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, priority, importance, energy, deadline, project_id),
    )
    task_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    p_icon = PRIORITY_ICONS.get(priority, "⚪")
    e_icon = ENERGY_ICONS.get(energy, "")
    dl_str = f" (до {deadline})" if deadline else ""
    pr_str = f" [проект {project_id}]" if project_id else ""
    return f"✅ [{task_id}] {p_icon}{e_icon} {title}{dl_str}{pr_str}"


def get_tasks(
    show_completed: bool = False,
    energy: str = None,
    project_id: int = None,
) -> str:
    conn = get_conn()
    c = conn.cursor()
    conditions = ["t.completed = %s"]
    values     = [1 if show_completed else 0]
    if energy:     conditions.append("t.energy = %s");     values.append(energy)
    if project_id: conditions.append("t.project_id = %s"); values.append(project_id)
    where = " AND ".join(conditions)
    c.execute(f"""
        SELECT t.*, p.name AS project_name
        FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
        WHERE {where}
        ORDER BY CASE t.priority   WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 CASE t.importance WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 t.created_at
    """, values)
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "📋 Нет задач"
    lines = ["📋 Задачи:\n"]
    for row in rows:
        p_icon = PRIORITY_ICONS.get(row["priority"], "⚪")
        e_icon = ENERGY_ICONS.get(row["energy"], "")
        _dl    = row["deadline"]
        dl     = f" — до {_dl}" if _dl and _dl not in ("null", "None", "undefined") else ""
        proj   = f" [{row['project_name']}]" if row["project_name"] else ""
        lines.append(f"{p_icon}{e_icon} [{row['id']}] {row['title']}{dl}{proj}")
    return "\n".join(lines)


def complete_task(task_id: int) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE tasks SET completed = 1, completed_at = NOW() WHERE id = %s", (task_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return f"✅ Задача [{task_id}] выполнена!" if affected else f"❌ Задача [{task_id}] не найдена"


def clear_tasks() -> str:
    """Mark ALL active tasks as completed at once."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE tasks SET completed = 1, completed_at = NOW() WHERE completed = 0")
    count = c.rowcount
    conn.commit()
    conn.close()
    return f"✅ Закрыто {count} задач" if count else "📋 Нет активных задач"


# ─── Notes ─────────────────────────────────────────────────────────────────────

def save_note(content: str, tags: str = "") -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO notes (content, tags) VALUES (%s, %s) RETURNING id", (content, tags))
    note_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    tags_str = f" #{tags.replace(',', ' #')}" if tags else ""
    return f"📝 Заметка [{note_id}]{tags_str}"


def search_notes(query: str) -> str:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, content, tags, created_at FROM notes
            WHERE to_tsvector('simple', content || ' ' || COALESCE(tags, ''))
                  @@ plainto_tsquery('simple', %s)
            ORDER BY created_at DESC LIMIT 5
        """, (query,))
        rows = c.fetchall()
    except Exception as e:
        conn.close()
        return f"❌ Ошибка поиска: {e}"
    conn.close()
    if not rows:
        return f"🔍 Ничего по запросу: «{query}»"
    lines = [f"🔍 Найдено {len(rows)}:\n"]
    for row in rows:
        preview  = row["content"][:120] + ("…" if len(row["content"]) > 120 else "")
        tags_str = f" #{row['tags']}" if row["tags"] else ""
        lines.append(f"[{row['id']}] {preview}\n   {str(row['created_at'])[:10]}{tags_str}\n")
    return "\n".join(lines)


# ─── Reminders ─────────────────────────────────────────────────────────────────

def create_reminder(title: str, remind_at: str) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO reminders (title, remind_at) VALUES (%s, %s) RETURNING id", (title, remind_at))
    rid = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return f"⏰ [{rid}] {title} — {remind_at}"


def get_reminders() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM reminders WHERE sent = 0 ORDER BY remind_at LIMIT 10")
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "⏰ Нет напоминаний"
    lines = ["⏰ Напоминания:\n"]
    for row in rows:
        lines.append(f"[{row['id']}] {row['title']}\n   🕐 {row['remind_at']}\n")
    return "\n".join(lines)


def get_due_reminders(now: str) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM reminders WHERE sent = 0 AND remind_at <= %s", (now,))
    rows = c.fetchall()
    conn.close()
    return rows


def mark_reminder_sent(reminder_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET sent = 1 WHERE id = %s", (reminder_id,))
    conn.commit()
    conn.close()


def delete_reminder(reminder_id: int) -> str:
    """Hard-delete one reminder by ID."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return (
        f"✅ Напоминание [{reminder_id}] удалено"
        if affected else
        f"❌ Напоминание [{reminder_id}] не найдено"
    )


def clear_reminders() -> str:
    """Hard-delete ALL reminders (sent and unsent)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM reminders")
    count = c.rowcount
    conn.commit()
    conn.close()
    return f"✅ Удалено {count} напоминаний" if count else "⏰ Напоминаний не было"


# ─── Pinned facts ──────────────────────────────────────────────────────────────

def pin_fact(content: str) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO pinned_facts (content) VALUES (%s)", (content,))
    conn.commit()
    conn.close()
    return f"📌 Запомнил: {content}"


def load_pinned_facts() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT content FROM pinned_facts ORDER BY created_at DESC LIMIT 20")
    rows = c.fetchall()
    conn.close()
    return "\n".join(f"• {r['content']}" for r in rows) if rows else ""


# ─── Profile ───────────────────────────────────────────────────────────────────

def get_profile() -> dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT key, value FROM profile")
    rows = c.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_profile(key: str, value: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO profile (key, value, updated_at) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (key, value),
    )
    conn.commit()
    conn.close()


# ─── Message history ───────────────────────────────────────────────────────────

def get_last_messages(n: int = 6) -> list[dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT %s", (n,))
    rows = c.fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_message(role: str, content: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO messages (role, content) VALUES (%s, %s)", (role, content))
    conn.commit()
    conn.close()


def clear_history():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM messages")
    conn.commit()
    conn.close()


# ─── Summaries ─────────────────────────────────────────────────────────────────

def count_messages_since_last_summary() -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT created_at FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        c.execute("SELECT COUNT(*) as count FROM messages WHERE created_at > %s", (row["created_at"],))
    else:
        c.execute("SELECT COUNT(*) as count FROM messages")
    count = c.fetchone()["count"]
    conn.close()
    return count


def get_messages_for_summary(limit: int = 20) -> list[dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT created_at FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        c.execute("SELECT role, content FROM messages WHERE created_at > %s ORDER BY id LIMIT %s",
                  (row["created_at"], limit))
    else:
        c.execute("SELECT role, content FROM messages ORDER BY id LIMIT %s", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def save_summary(content: str, messages_count: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO conversation_summaries (content, messages_count) VALUES (%s, %s)",
              (content, messages_count))
    conn.commit()
    conn.close()


def get_latest_summary() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT content FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row["content"] if row else ""
