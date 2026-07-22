import json
import logging
import re
from datetime import datetime

from config import TASKS_LIMIT
from constants import InboxStatus, InboxType
from ctx import current_user_id
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
    query = (query or "").strip()
    with db() as c:
        if not query:
            # Если просят "показать все" — отдаём последние 10 заметок
            c.execute("SELECT id, content, tags, created_at FROM notes ORDER BY created_at DESC LIMIT 10")
            rows = c.fetchall()
            if not rows:
                return "🔍 Заметок пока нет."
            lines = ["📝 Последние заметки:\n"]
            for row in rows:
                preview  = row["content"][:120] + ("…" if len(row["content"]) > 120 else "")
                tags_str = f" #{row['tags']}" if row["tags"] else ""
                lines.append(f"[{row['id']}] {preview}\n   {str(row['created_at'])[:10]}{tags_str}\n")
            return "\n".join(lines)

        # 'russian' stems, so «молока» matches a note saying «молоко», and stopwords
        # like «как» stop being mandatory AND-terms.
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


# ─── Universal Inbox ───────────────────────────────────────────────────────────
# Страховочная сетка: сюда падает только то, что LLM не смог уверенно отнести
# к задаче/заметке/расходу/здоровью. Основной путь — прямое создание в нужном
# модуле (правило в системном промпте). Inbox — fallback, не «главная папка».

INBOX_LIMIT = 20  # верхняя граница выдачи /inbox (читаемость и токены)

_TYPE_ICONS = {
    InboxType.TEXT: "💬",
    InboxType.VOICE: "🎤",
    InboxType.IMAGE: "🖼",
    InboxType.DOCUMENT: "📎",
    InboxType.FORWARD: "↪️",
}
_STATUS_ICONS = {
    InboxStatus.INBOX: "📥",
    InboxStatus.TASK: "✅→задача",
    InboxStatus.NOTE: "✅→заметка",
    InboxStatus.EXPENSE: "✅→расход",
    InboxStatus.HEALTH: "✅→здоровье",
    InboxStatus.DONE: "✔️ закрыто",
}


def add_inbox(content: str, type: str = InboxType.TEXT, metadata: dict = None) -> str:
    """Бросить запись в Inbox. user_id берётся из контекста, не из аргументов.

    metadata — произвольный JSONB (file_id, duration, mime, photo…). Если
    передан не-словарь — сохраняем как '{}', лучше пустой JSON, чем упавший INSERT.
    """
    if type not in InboxType.ALL:
        return f"❌ Неизвестный тип inbox: {type!r}"
    meta_str = json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, dict) else "{}"
    uid = current_user_id()
    with db() as c:
        c.execute(
            """INSERT INTO inbox (user_id, type, content, metadata)
               VALUES (%s, %s, %s, %s::jsonb) RETURNING id""",
            (uid, type, content, meta_str),
        )
        iid = c.fetchone()["id"]
    icon = _TYPE_ICONS.get(type, "📥")
    return f"{icon} В Inbox [{iid}]: {content}"


def get_inbox(status: str = InboxStatus.INBOX) -> str:
    """Показать записи Inbox. По умолчанию — нераспределённые (status=inbox)."""
    if status not in InboxStatus.ALL:
        return f"❌ Неизвестный статус inbox: {status!r}"
    uid = current_user_id()
    with db() as c:
        c.execute(
            "SELECT id, type, content, metadata, status, created_at "
            "FROM inbox WHERE user_id = %s AND status = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (uid, status, INBOX_LIMIT + 1),
        )
        rows = c.fetchall()
    if not rows:
        label = "нераспределённые" if status == InboxStatus.INBOX else f"со статусом «{status}»"
        return f"📥 В Inbox {label} записи отсутствуют."
    truncated = len(rows) > INBOX_LIMIT
    rows = rows[:INBOX_LIMIT]

    header = "📥" if status == InboxStatus.INBOX else f"📥 (статус: {status})"
    lines = [f"{header} Inbox:\n"]
    for row in rows:
        icon = _TYPE_ICONS.get(row["type"], "📥")
        preview = row["content"][:140] + ("…" if len(row["content"]) > 140 else "")
        date_str = str(row["created_at"])[:10]
        lines.append(f"{icon} [{row['id']}] {preview}\n   {date_str}")
    if truncated:
        lines.append(f"\n… показаны первые {INBOX_LIMIT}.")
    return "\n".join(lines)


def resolve_inbox(item_id: int, action: str = InboxStatus.DONE) -> str:
    """Отметить запись Inbox как обработанную (статус task/note/done/...).

    Сама запись НЕ создаёт задачу/заметку elsewhere — это лишь отметка, что
    пользователь вручную разобрал запись. Прямое создание делает LLM через
    create_task/save_note/..., а сюда запись попадает если уже создана.
    """
    if action not in InboxStatus.ALL:
        return f"❌ Неизвестное действие: {action!r}"
    uid = current_user_id()
    with db() as c:
        c.execute(
            "UPDATE inbox SET status = %s WHERE id = %s AND user_id = %s RETURNING content",
            (action, item_id, uid),
        )
        row = c.fetchone()
    if not row:
        return f"❌ Запись [{item_id}] не найдена в твоём Inbox."
    label = _STATUS_ICONS.get(action, action)
    return f"{label}: «{row['content'][:80]}»"


def get_inbox_count() -> int:
    """Сколько нераспределённых записей в Inbox текущего пользователя.
    Для Dashboard — число, а не строка."""
    uid = current_user_id()
    with db() as c:
        c.execute(
            "SELECT COUNT(*) AS n FROM inbox WHERE user_id = %s AND status = 'inbox'",
            (uid,),
        )
        return c.fetchone()["n"]


# ─── Goals ─────────────────────────────────────────────────────────────────────

GOALS_LIMIT = 25
GOAL_STATUS_ICONS = {"active": "🎯", "done": "✅", "paused": "⏸"}
GOAL_PRIORITY_ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
_PRIORITY_ORDER = {"high": 1, "medium": 2, "low": 3}


def create_goal(
    title: str,
    description: str = "",
    deadline: str = None,
    priority: str = "medium",
    kpi: str = "",
) -> str:
    """Создать цель. priority/status имеют дефолты, чтобы LLM мог вызвать с минимумом."""
    if priority not in ("high", "medium", "low"):
        return f"❌ Неизвестный приоритет: {priority!r}"
    uid = current_user_id()
    with db() as c:
        c.execute(
            """INSERT INTO goals (user_id, title, description, deadline, priority, kpi)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (uid, title, description, deadline, priority, kpi),
        )
        gid = c.fetchone()["id"]
    p_icon = GOAL_PRIORITY_ICONS.get(priority, "⚪")
    dl = f" (до {deadline})" if deadline else ""
    return f"🎯 Цель [{gid}] {p_icon} {title}{dl}"


def get_goals(status_filter: str = None) -> str:
    """Показать цели. По умолчанию — активные, отсортированы по приоритету."""
    if status_filter and status_filter not in ("active", "done", "paused"):
        return f"❌ Неизвестный статус: {status_filter!r}"
    uid = current_user_id()
    with db() as c:
        if status_filter:
            c.execute(
                """SELECT id, title, description, deadline, status, priority, kpi, progress,
                          created_at
                   FROM goals WHERE user_id = %s AND status = %s
                   ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                            created_at LIMIT %s""",
                (uid, status_filter, GOALS_LIMIT + 1),
            )
        else:
            # без фильтра — только активные (архив done/paused по запросу)
            c.execute(
                """SELECT id, title, description, deadline, status, priority, kpi, progress,
                          created_at
                   FROM goals WHERE user_id = %s AND status = 'active'
                   ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                            created_at LIMIT %s""",
                (uid, GOALS_LIMIT + 1),
            )
        rows = c.fetchall()
    if not rows:
        return "🎯 Активных целей нет"
    truncated = len(rows) > GOALS_LIMIT
    rows = rows[:GOALS_LIMIT]

    # Подгружаем связанные проекты одним запросом
    gids = [r["id"] for r in rows]
    proj_map: dict[int, list[str]] = {}
    if gids:
        with db() as c:
            c.execute(
                """SELECT gp.goal_id, p.name
                   FROM goal_projects gp JOIN projects p ON p.id = gp.project_id
                   WHERE gp.goal_id = ANY(%s)""",
                (gids,),
            )
            for row in c.fetchall():
                proj_map.setdefault(row["goal_id"], []).append(row["name"])

    header = "🎯 Цели:" if not status_filter else f"🎯 Цели ({status_filter}):"
    lines = [header + "\n"]
    for r in rows:
        p_icon = GOAL_PRIORITY_ICONS.get(r["priority"], "⚪")
        bar = _progress_bar(r["progress"])
        dl = f" — до {r['deadline']}" if r["deadline"] else ""
        lines.append(f"{p_icon} [{r['id']}] {r['title']}{dl} {bar}")
        if r["kpi"]:
            lines.append(f"   📊 KPI: {r['kpi']}")
        if r["description"]:
            preview = r["description"][:120] + ("…" if len(r["description"]) > 120 else "")
            lines.append(f"   {preview}")
        if r["id"] in proj_map:
            lines.append(f"   🔗 Проекты: {', '.join(proj_map[r['id']])}")
    if truncated:
        lines.append(f"\n… показаны первые {GOALS_LIMIT}.")
    return "\n".join(lines)


def update_goal(
    goal_id: int,
    status: str = None,
    priority: str = None,
    progress: int = None,
    kpi: str = None,
    description: str = None,
    deadline: str = None,
) -> str:
    """Обновить поля цели. progress 0-100; при status='done' progress=100 автоматически."""
    if status and status not in ("active", "done", "paused"):
        return f"❌ Неизвестный статус: {status!r}"
    if priority and priority not in ("high", "medium", "low"):
        return f"❌ Неизвестный приоритет: {priority!r}"
    if progress is not None and not (0 <= progress <= 100):
        return f"❌ Прогресс должен быть 0-100, получено {progress}"

    uid = current_user_id()
    updates, values = [], []
    if status is not None:
        updates.append("status = %s")
        values.append(status)
        if status == "done":
            updates.append("progress = 100")
    if priority is not None:
        updates.append("priority = %s")
        values.append(priority)
    if progress is not None:
        updates.append("progress = %s")
        values.append(progress)
    if kpi is not None:
        updates.append("kpi = %s")
        values.append(kpi)
    if description is not None:
        updates.append("description = %s")
        values.append(description)
    if deadline is not None:
        updates.append("deadline = %s")
        values.append(deadline)
    if not updates:
        return "❌ Нечего обновлять"

    values += [goal_id, uid]
    with db() as c:
        c.execute(
            f"UPDATE goals SET {', '.join(updates)} WHERE id = %s AND user_id = %s RETURNING title",
            values,
        )
        row = c.fetchone()
    if not row:
        return f"❌ Цель [{goal_id}] не найдена"
    return f"✅ Цель [{goal_id}] обновлена"


def link_goal_project(goal_id: int, project_id: int) -> str:
    """Связать цель с проектом (многие-ко-многим). Оба id валидируются."""
    uid = current_user_id()
    with db() as c:
        # Цель — у текущего пользователя
        c.execute("SELECT 1 FROM goals WHERE id = %s AND user_id = %s", (goal_id, uid))
        if c.fetchone() is None:
            return f"❌ Цель [{goal_id}] не найдена"
        # Проект — глобальный (в projects нет user_id по решению Phase 1.0)
        c.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        prow = c.fetchone()
        if prow is None:
            return f"❌ Проект [{project_id}] не найден"
        c.execute(
            "INSERT INTO goal_projects (goal_id, project_id) VALUES (%s, %s) "
            "ON CONFLICT (goal_id, project_id) DO NOTHING",
            (goal_id, project_id),
        )
    return f"🔗 Цель [{goal_id}] ↔ проект «{prow['name']}»"


def get_active_goals_summary() -> str:
    """Краткий блок для системного промпта (одна строка на цель, как с проектами)."""
    uid = current_user_id()
    with db() as c:
        c.execute(
            """SELECT id, title, priority, progress
               FROM goals WHERE user_id = %s AND status = 'active'
               ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                        created_at LIMIT 10""",
            (uid,),
        )
        rows = c.fetchall()
    if not rows:
        return ""
    icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = []
    for r in rows:
        icon = icons.get(r["priority"], "•")
        lines.append(f"{icon} [{r['id']}] {r['title']} ({r['progress']}%)")
    return "\n".join(lines)


def _progress_bar(progress: int) -> str:
    """10-сегментный текстовый прогресс-бар: ▰▰▰▰▱▱▱▱▱▱"""
    filled = max(0, min(10, round(progress / 10)))
    return "▰" * filled + "▱" * (10 - filled) + f" {progress}%"


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
