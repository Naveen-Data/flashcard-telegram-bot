import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "cards.db"
INTERVALS_DAYS: list[int] = [1, 2, 5, 12, 26, 45]


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # safe concurrent access from bot + mcp_server
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                stage INTEGER NOT NULL DEFAULT 0,
                due_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                chat_id INTEGER NOT NULL,
                tags TEXT DEFAULT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Migration: add tags column for existing databases
        try:
            conn.execute("ALTER TABLE cards ADD COLUMN tags TEXT DEFAULT NULL")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def add_card(question: str, answer: str, chat_id: int, tags: Optional[str] = None) -> int:
    now = datetime.utcnow()
    due_at = (now + timedelta(days=INTERVALS_DAYS[0])).isoformat()
    with _get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags)"
            " VALUES (?, ?, 0, ?, ?, ?, ?)",
            (question, answer, due_at, now.isoformat(), chat_id, tags),
        )
        conn.commit()
        return cursor.lastrowid


def add_cards_bulk(cards: list[dict], chat_id: int) -> list[int]:
    if not cards:
        return []
    now = datetime.utcnow()
    due_at = (now + timedelta(days=INTERVALS_DAYS[0])).isoformat()
    now_iso = now.isoformat()
    ids: list[int] = []
    with _get_connection() as conn:
        for card in cards:
            cursor = conn.execute(
                "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags)"
                " VALUES (?, ?, 0, ?, ?, ?, ?)",
                (card["question"], card["answer"], due_at, now_iso, chat_id, card.get("tags")),
            )
            ids.append(cursor.lastrowid)
        conn.commit()
    return ids


def list_due_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        if tag:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id = ? AND due_at <= ?"
                " AND ',' || tags || ',' LIKE ?",
                (chat_id, now, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id = ? AND due_at <= ?",
                (chat_id, now),
            ).fetchall()
        return [dict(row) for row in rows]


def list_all_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    with _get_connection() as conn:
        if tag:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id = ?"
                " AND ',' || tags || ',' LIKE ?",
                (chat_id, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id = ?",
                (chat_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def list_tags(chat_id: int) -> list[tuple[str, int, int]]:
    """Return (tag, total_cards, due_cards) sorted by tag name."""
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT tags, due_at FROM cards WHERE chat_id = ? AND tags IS NOT NULL",
            (chat_id,),
        ).fetchall()
    tag_totals: dict[str, int] = {}
    tag_due: dict[str, int] = {}
    for row in rows:
        for tag in row["tags"].split(","):
            tag = tag.strip()
            if not tag:
                continue
            tag_totals[tag] = tag_totals.get(tag, 0) + 1
            if row["due_at"] <= now:
                tag_due[tag] = tag_due.get(tag, 0) + 1
    return sorted(
        [(tag, total, tag_due.get(tag, 0)) for tag, total in tag_totals.items()],
        key=lambda x: x[0],
    )


def get_stats(chat_id: int, tag: Optional[str] = None) -> dict:
    now = datetime.utcnow().isoformat()
    tag_filter = " AND ',' || tags || ',' LIKE ?" if tag else ""
    tag_params: tuple = (f"%,{tag},%",) if tag else ()
    with _get_connection() as conn:
        total: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id = ?{tag_filter}",
            (chat_id,) + tag_params,
        ).fetchone()[0]
        due: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id = ? AND due_at <= ?{tag_filter}",
            (chat_id, now) + tag_params,
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT stage, COUNT(*) AS count FROM cards WHERE chat_id = ?{tag_filter} GROUP BY stage",
            (chat_id,) + tag_params,
        ).fetchall()
        by_stage: dict[int, int] = {row["stage"]: row["count"] for row in rows}
    return {"total": total, "due": due, "by_stage": by_stage}


def get_registered_chat_id() -> Optional[int]:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'chat_id'"
        ).fetchone()
        return int(row["value"]) if row else None


def set_registered_chat_id(chat_id: int) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)",
            (str(chat_id),),
        )
        conn.commit()


def record_answer(card_id: int, correct: bool) -> None:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT stage FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            return
        current_stage: int = row["stage"]
        new_stage = min(current_stage + 1, len(INTERVALS_DAYS) - 1) if correct else 0
        due_at = (datetime.utcnow() + timedelta(days=INTERVALS_DAYS[new_stage])).isoformat()
        conn.execute(
            "UPDATE cards SET stage = ?, due_at = ? WHERE id = ?",
            (new_stage, due_at, card_id),
        )
        conn.commit()


def snooze_card(card_id: int, delta: timedelta) -> None:
    new_due = (datetime.utcnow() + delta).isoformat()
    with _get_connection() as conn:
        conn.execute(
            "UPDATE cards SET due_at = ? WHERE id = ?",
            (new_due, card_id),
        )
        conn.commit()


def get_card(card_id: int) -> Optional[dict]:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return dict(row) if row else None
