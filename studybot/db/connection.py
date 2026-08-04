import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "cards.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                stage INTEGER NOT NULL DEFAULT 0,
                due_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                chat_id INTEGER NOT NULL,
                tags TEXT DEFAULT NULL,
                ease_factor REAL NOT NULL DEFAULT 2.5,
                interval_days INTEGER NOT NULL DEFAULT 1,
                repetitions INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (chat_id, key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                quality INTEGER NOT NULL,
                reviewed_at DATETIME NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS undo_snapshots (
                chat_id INTEGER PRIMARY KEY,
                card_id INTEGER NOT NULL,
                ease_factor REAL NOT NULL,
                interval_days INTEGER NOT NULL,
                repetitions INTEGER NOT NULL,
                due_at DATETIME NOT NULL,
                stage INTEGER NOT NULL,
                consecutive_again INTEGER NOT NULL,
                review_log_id INTEGER,
                created_at DATETIME NOT NULL
            )
        """)
        for col, typedef in [
            ("tags", "TEXT DEFAULT NULL"),
            ("ease_factor", "REAL NOT NULL DEFAULT 2.5"),
            ("interval_days", "INTEGER NOT NULL DEFAULT 1"),
            ("repetitions", "INTEGER NOT NULL DEFAULT 0"),
            ("card_type", "TEXT NOT NULL DEFAULT 'basic'"),
            ("image_file_id", "TEXT DEFAULT NULL"),
            ("consecutive_again", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
