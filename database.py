import logging
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import DATABASE_URL, DB_POOL_MAX, TIMEZONE

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def _configure(conn):
    """Pin every pooled connection to the app timezone so CURRENT_TIMESTAMP/NOW()
    agree with config.now_local(). TIMEZONE is validated by ZoneInfo at import."""
    conn.execute(f"SET TIME ZONE '{TIMEZONE}'")


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=DB_POOL_MAX,
            configure=_configure,
            kwargs={"row_factory": dict_row},
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

    logger.info("Schema ready")
