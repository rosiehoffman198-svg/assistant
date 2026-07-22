import logging
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import DATABASE_URL, DB_POOL_MAX, TIMEZONE

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # Timezone is set as a libpq startup option instead of a per-connection
        # `SET TIME ZONE`. A configure function that runs SET leaves the fresh
        # connection in INTRANS, which psycopg_pool rejects ("connection left in
        # status INTRANS by configure function: discarded") — it then discards
        # every connection and the pool can never hand one out. The startup
        # option pins the zone with no open transaction and no extra round-trip.
        # TIMEZONE is a valid IANA name (validated by ZoneInfo at import), so it
        # is safe to inline here.
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=DB_POOL_MAX,
            kwargs={
                "row_factory": dict_row,
                "options": f"-c timezone={TIMEZONE}",
            },
            open=True,
        )
    return _pool


@contextmanager
def db():
    """Cursor with guaranteed commit/rollback/return-to-pool.

    Replaces the old get_conn() pattern where any failing execute() skipped
    conn.close() and leaked a server-side connection.
    """
    with get_pool().connection() as conn:
        with conn.cursor() as c:
            yield c


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_db():
    """Creates missing tables/columns/indexes only.

    Deliberately does NOT rewrite existing data: no column-type changes, no
    DROP, no DELETE, no UPDATE. Everything here is additive and safe to run
    against a database that other things also use.
    """
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS profile (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         SERIAL PRIMARY KEY,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                status     TEXT DEFAULT 'active',
                goal       TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id           SERIAL PRIMARY KEY,
                title        TEXT NOT NULL,
                priority     TEXT DEFAULT 'medium',
                importance   TEXT DEFAULT 'medium',
                energy       TEXT DEFAULT 'medium',
                deadline     TEXT,
                completed    INTEGER DEFAULT 0,
                completed_at TIMESTAMP,
                project_id   INTEGER,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Additive only — same as before. No FK is added on project_id: a bad id
        # is rejected in create_task() instead, so existing rows stay untouched.
        for stmt in [
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS importance   TEXT DEFAULT 'medium'",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS energy       TEXT DEFAULT 'medium'",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project_id   INTEGER",
        ]:
            c.execute(stmt)

        c.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         SERIAL PRIMARY KEY,
                content    TEXT NOT NULL,
                tags       TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # The old 'simple' index is left in place. This one matches the
        # 'russian' expression search_notes() now uses, so the query is indexed
        # rather than falling back to a sequential scan. Creating an index does
        # not touch row data.
        c.execute("""
            CREATE INDEX IF NOT EXISTS notes_fts_ru_idx
            ON notes USING GIN (
                to_tsvector('russian', content || ' ' || COALESCE(tags, ''))
            )
        """)

        # remind_at stays TEXT. Correctness comes from the app instead:
        # create_reminder() writes a canonical zero-padded "YYYY-MM-DD HH:MM",
        # which sorts lexicographically the same way it sorts chronologically,
        # and get_due_reminders() only compares rows matching that shape.
        c.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id         SERIAL PRIMARY KEY,
                title      TEXT NOT NULL,
                remind_at  TEXT NOT NULL,
                sent       INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS pinned_facts (
                id         SERIAL PRIMARY KEY,
                content    TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id              SERIAL PRIMARY KEY,
                content         TEXT NOT NULL,
                messages_count  INTEGER DEFAULT 0,
                last_message_id INTEGER DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Additive: tracking the last summarized message id instead of a
        # wall-clock cutoff, which used to drop messages saved while the
        # summarization call was still running.
        c.execute(
            "ALTER TABLE conversation_summaries "
            "ADD COLUMN IF NOT EXISTS last_message_id INTEGER DEFAULT 0"
        )

        # ─── Universal Inbox ─────────────────────────────────────────────────
        # Страховочная сетка: сюда падает только то, что LLM не смог уверенно
        # отнести к задаче/заметке/расходу/здоровью (см. правило в системном
        # промпте). metadata — JSONB под любые атрибуты (file_id, duration…).
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS inbox (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                type        TEXT    NOT NULL DEFAULT 'text'
                            CHECK (type IN ('text','voice','image','document','forward')),
                content     TEXT    NOT NULL,
                metadata    JSONB   NOT NULL DEFAULT '{{}}'::jsonb,
                status      TEXT    NOT NULL DEFAULT 'inbox'
                            CHECK (status IN ('inbox','task','note','expense','health','done')),
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS inbox_user_status_idx "
            "ON inbox (user_id, status, created_at DESC)"
        )
        add_updated_at_trigger("inbox")

        # ─── Goals ────────────────────────────────────────────────────────────
        # Цель живёт отдельно от задач/проектов и связана с проектами
        # многие-ко-многим (цель «Запустить MVP» ↔ Backend / Bot / Landing).
        c.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                title       TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                deadline    TEXT,
                status      TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','done','paused')),
                priority    TEXT    NOT NULL DEFAULT 'medium'
                            CHECK (priority IN ('high','medium','low')),
                kpi         TEXT    NOT NULL DEFAULT '',
                progress    INTEGER NOT NULL DEFAULT 0
                            CHECK (progress >= 0 AND progress <= 100),
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS goals_user_status_idx "
            "ON goals (user_id, status, created_at DESC)"
        )
        add_updated_at_trigger("goals")

        # Связка цель ↔ проект (многие-ко-многим). Без FK на проект/цель —
        # id валидируется в link_goal_project(), как с tasks.project_id.
        c.execute("""
            CREATE TABLE IF NOT EXISTS goal_projects (
                goal_id     INTEGER NOT NULL,
                project_id  INTEGER NOT NULL,
                PRIMARY KEY (goal_id, project_id)
            )
        """)

        # ─── Audit: автообновление updated_at ────────────────────────────────
        # Одна PL/pgSQL-функция на все таблицы: проставляет updated_at = NOW()
        # при любом UPDATE. Триггеры на конкретные таблицы навешиваются
        # additively через add_updated_at_trigger() — по одной строке на таблицу,
        # никаких хитрых CHECK перед CREATE TRIGGER (их до PG14 нет).
        c.execute("""
            CREATE OR REPLACE FUNCTION touch_updated_at()
            RETURNS trigger AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        # Регистрируем таблицы, которым нужен updated_at. Пока список пустой —
        # модули данных (Phase 1) допишут сюда свои имена через add_updated_at_trigger().
        c.execute(
            "CREATE TABLE IF NOT EXISTS _audit_tracked_tables "
            "(table_name TEXT PRIMARY KEY)"
        )

    logger.info("Schema ready")


def add_updated_at_trigger(table_name: str):
    """Навесить триггер touch_updated_at() на таблицу. Идемпотентно.

    CREATE TRIGGER не имеет IF NOT EXISTS до Postgres 14, поэтому фиксируем
    факт создания в служебной таблице _audit_tracked_tables и пропускаем
    повторное создание. Без этого запуск с уже существующей таблицей падал бы.
    """
    with db() as c:
        # INSERT ... ON CONFLICT DO NOTHING фиксирует, что триггер уже навешен.
        c.execute(
            "INSERT INTO _audit_tracked_tables (table_name) VALUES (%s) "
            "ON CONFLICT (table_name) DO NOTHING "
            "RETURNING table_name",
            (table_name,),
        )
        if c.fetchone() is None:
            return  # уже было создано ранее — пропускаем
        c.execute(
            f"CREATE TRIGGER trg_{table_name}_updated_at "
            f"BEFORE UPDATE ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION touch_updated_at()"
        )
