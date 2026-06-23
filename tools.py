from database import get_conn

PRIORITY_ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}


# ─── Tasks ────────────────────────────────────────────────────────────────────

def create_task(title: str, priority: str = "medium", deadline: str = None) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (title, priority, deadline) VALUES (?, ?, ?)",
        (title, priority, deadline),
    )
    task_id = c.lastrowid
    conn.commit()
    conn.close()
    icon = PRIORITY_ICONS.get(priority, "⚪")
    deadline_str = f" (до {deadline})" if deadline else ""
    return f"✅ Задача создана [{task_id}]: {icon} {title}{deadline_str}"


def get_tasks(show_completed: bool = False) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM tasks WHERE completed = ? ORDER BY "
        "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at",
        (1 if show_completed else 0,),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "📋 Нет активных задач"
    lines = ["📋 Задачи:\n"]
    for row in rows:
        icon = PRIORITY_ICONS.get(row["priority"], "⚪")
        dl = f" — до {row['deadline']}" if row["deadline"] else ""
        lines.append(f"{icon} [{row['id']}] {row['title']}{dl}")
    return "\n".join(lines)


def complete_task(task_id: int) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
    if c.rowcount == 0:
        conn.close()
        return f"❌ Задача [{task_id}] не найдена"
    conn.commit()
    conn.close()
    return f"✅ Задача [{task_id}] выполнена!"


# ─── Notes ────────────────────────────────────────────────────────────────────

def save_note(content: str, tags: str = "") -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO notes (content, tags) VALUES (?, ?)", (content, tags))
    note_id = c.lastrowid
    # Keep FTS index in sync
    c.execute(
        "INSERT INTO notes_fts (content, tags, note_id) VALUES (?, ?, ?)",
        (content, tags, note_id),
    )
    conn.commit()
    conn.close()
    tags_str = f" #{tags.replace(',', ' #')}" if tags else ""
    return f"📝 Заметка сохранена [{note_id}]{tags_str}"


def search_notes(query: str) -> str:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT n.id, n.content, n.tags, n.created_at
            FROM notes n
            JOIN notes_fts f ON n.id = f.note_id
            WHERE notes_fts MATCH ?
            ORDER BY rank LIMIT 5
            """,
            (query,),
        )
        rows = c.fetchall()
    except Exception:
        conn.close()
        return "❌ Ошибка поиска. Попробуй другой запрос."
    conn.close()
    if not rows:
        return f"🔍 Ничего не найдено по запросу: «{query}»"
    lines = [f"🔍 Найдено {len(rows)} заметок:\n"]
    for row in rows:
        preview = row["content"][:120] + ("…" if len(row["content"]) > 120 else "")
        tags_str = f" #{row['tags']}" if row["tags"] else ""
        lines.append(f"[{row['id']}] {preview}\n   {row['created_at'][:10]}{tags_str}\n")
    return "\n".join(lines)


# ─── Reminders ────────────────────────────────────────────────────────────────

def create_reminder(title: str, remind_at: str) -> str:
    """remind_at format: YYYY-MM-DD HH:MM"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reminders (title, remind_at) VALUES (?, ?)",
        (title, remind_at),
    )
    reminder_id = c.lastrowid
    conn.commit()
    conn.close()
    return f"⏰ Напоминание [{reminder_id}]: {title} — {remind_at}"


def get_reminders() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM reminders WHERE sent = 0 ORDER BY remind_at LIMIT 10"
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "⏰ Нет активных напоминаний"
    lines = ["⏰ Напоминания:\n"]
    for row in rows:
        lines.append(f"[{row['id']}] {row['title']}\n   🕐 {row['remind_at']}\n")
    return "\n".join(lines)


# ─── Pinned facts ─────────────────────────────────────────────────────────────

def pin_fact(content: str) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO pinned_facts (content) VALUES (?)", (content,))
    conn.commit()
    conn.close()
    return f"📌 Запомнил: {content}"


def load_pinned_facts() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT content FROM pinned_facts ORDER BY created_at DESC LIMIT 20")
    rows = c.fetchall()
    conn.close()
    if not rows:
        return ""
    return "\n".join(f"• {row['content']}" for row in rows)


# ─── Profile ──────────────────────────────────────────────────────────────────

def get_profile() -> dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT key, value FROM profile")
    rows = c.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def set_profile(key: str, value: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO profile (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ─── Message history ──────────────────────────────────────────────────────────

def get_last_messages(n: int = 6) -> list[dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (n,)
    )
    rows = c.fetchall()
    conn.close()
    # Reverse so oldest first (correct order for LLM)
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def save_message(role: str, content: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    conn.commit()
    conn.close()


def clear_history():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM messages")
    conn.commit()
    conn.close()


# ─── Conversation summaries ───────────────────────────────────────────────────

def count_messages_since_last_summary() -> int:
    """How many messages have accumulated since the last saved summary."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT created_at FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        c.execute("SELECT COUNT(*) FROM messages WHERE created_at > ?", (row["created_at"],))
    else:
        c.execute("SELECT COUNT(*) FROM messages")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_messages_for_summary(limit: int = 20) -> list[dict]:
    """Messages accumulated since the last summary, up to `limit`."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT created_at FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        c.execute(
            "SELECT role, content FROM messages WHERE created_at > ? ORDER BY id LIMIT ?",
            (row["created_at"], limit),
        )
    else:
        c.execute("SELECT role, content FROM messages ORDER BY id LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def save_summary(content: str, messages_count: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO conversation_summaries (content, messages_count) VALUES (?, ?)",
        (content, messages_count),
    )
    conn.commit()
    conn.close()


def get_latest_summary() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT content FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row["content"] if row else ""
