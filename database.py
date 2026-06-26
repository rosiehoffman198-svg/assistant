import psycopg
from psycopg.rows import dict_row
from config import DATABASE_URL


def get_conn() -> psycopg.Connection:
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

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

    # ── Projects (new) ────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            status     TEXT DEFAULT 'active',
            goal       TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tasks — full schema for new installs
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

    # Migrations for existing tasks tables (safe to re-run)
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

    c.execute("""
        CREATE INDEX IF NOT EXISTS notes_fts_idx
        ON notes USING GIN (
            to_tsvector('simple', content || ' ' || COALESCE(tags, ''))
        )
    """)

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
            id             SERIAL PRIMARY KEY,
            content        TEXT NOT NULL,
            messages_count INTEGER DEFAULT 0,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
