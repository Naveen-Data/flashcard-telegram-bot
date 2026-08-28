from datetime import datetime
from typing import Optional

from studybot.db.connection import get_connection


def add_session_note(
    chat_id: int, topic: str, content: str, tags: Optional[str] = None
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO session_notes (chat_id, topic, content, tags, created_at)"
            " VALUES (?,?,?,?,?)",
            (chat_id, topic, content, tags, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def list_session_notes(chat_id: int, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM session_notes WHERE chat_id=? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
