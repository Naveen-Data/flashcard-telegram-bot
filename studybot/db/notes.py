"""Session notes — a study session's summary, separate from any one card."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from studybot.db.connection import get_connection


def add_session_note(
    user_id: int, topic: str, content: str, tags: Optional[str] = None,
    telegram_account_id: Optional[int] = None,
) -> int:
    with get_connection() as conn:
        return conn.execute(
            text(
                "INSERT INTO session_notes (user_id, telegram_account_id, topic, content, tags, created_at)"
                " VALUES (:uid, :tgid, :topic, :content, :tags, :created) RETURNING id"
            ),
            {
                "uid": user_id, "tgid": telegram_account_id, "topic": topic, "content": content,
                "tags": tags, "created": datetime.now(timezone.utc),
            },
        ).scalar_one()


def list_session_notes(user_id: int, limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT id, topic, content, tags, created_at FROM session_notes"
                " WHERE user_id=:uid ORDER BY created_at DESC LIMIT :lim"
            ),
            {"uid": user_id, "lim": limit},
        )
        return [dict(r._mapping) for r in rows]


def search_session_notes(user_id: int, keyword: str, limit: int = 50) -> list[dict]:
    pattern = f"%{keyword}%"
    with get_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT id, topic, content, tags, created_at FROM session_notes"
                " WHERE user_id=:uid AND (topic ILIKE :p OR content ILIKE :p OR tags ILIKE :p)"
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"uid": user_id, "p": pattern, "lim": limit},
        )
        return [dict(r._mapping) for r in rows]


def edit_session_note(
    user_id: int, note_id: int, topic: Optional[str] = None,
    content: Optional[str] = None, tags: Optional[str] = None,
) -> bool:
    sets, params = [], {"uid": user_id, "id": note_id}
    for col, val in (("topic", topic), ("content", content), ("tags", tags)):
        if val is not None:
            sets.append(f"{col}=:{col}")
            params[col] = val
    if not sets:
        return False
    with get_connection() as conn:
        result = conn.execute(
            text(f"UPDATE session_notes SET {', '.join(sets)} WHERE id=:id AND user_id=:uid"), params
        )
        return result.rowcount > 0


def delete_session_note(user_id: int, note_id: int) -> bool:
    with get_connection() as conn:
        result = conn.execute(
            text("DELETE FROM session_notes WHERE id=:id AND user_id=:uid"), {"id": note_id, "uid": user_id}
        )
        return result.rowcount > 0
