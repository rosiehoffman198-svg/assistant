import psycopg2
import psycopg2.extras
from config import DATABASE_URL


def get_conn() -> psycopg2.extensions.connection:
    """Return a psycopg2 connection with RealDictCursor as default."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
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

    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id         SERIAL PRIMARY KEY,
            title      TEXT NOT NULL,
            priority   TEXT DEFAULT 'medium',
            deadline   TEXT,
            completed  INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id         SERIAL PRIMARY KEY,
            content    TEXT NOT NULL,
            tags       TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # GIN index for full-text search (replaces SQLite FTS5)
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
