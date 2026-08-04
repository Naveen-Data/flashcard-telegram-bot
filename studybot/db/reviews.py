import json
from datetime import datetime, timedelta
from typing import Optional

from studybot.db.connection import get_connection

_IST = timedelta(hours=5, minutes=30)


def log_review(chat_id: int, card_id: int, quality: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO review_log (card_id, chat_id, quality, reviewed_at) VALUES (?,?,?,?)",
            (card_id, chat_id, quality, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def delete_review_log_entry(log_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM review_log WHERE id=?", (log_id,))
        conn.commit()


def get_card_history(card_id: int, limit: int = 5) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT quality, reviewed_at FROM review_log WHERE card_id=? ORDER BY reviewed_at DESC LIMIT ?",
            (card_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_weekly_stats(chat_id: int) -> dict:
    since = (datetime.utcnow() - timedelta(days=7)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT quality, reviewed_at FROM review_log WHERE chat_id=? AND reviewed_at>=?",
            (chat_id, since),
        ).fetchall()
    total = len(rows)
    correct = sum(1 for r in rows if r["quality"] >= 3)
    accuracy = round(correct / total * 100) if total else 0
    by_day: dict[str, int] = {}
    for r in rows:
        day = (datetime.fromisoformat(r["reviewed_at"]) + _IST).date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "active_days": len(by_day),
        "by_day": by_day,
    }


def save_undo_snapshot(
    chat_id: int, card_id: int, ease_factor: float, interval_days: int, repetitions: int,
    due_at: str, stage: int, consecutive_again: int, review_log_id: Optional[int],
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO undo_snapshots"
            " (chat_id, card_id, ease_factor, interval_days, repetitions, due_at, stage,"
            "  consecutive_again, review_log_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (chat_id, card_id, ease_factor, interval_days, repetitions, due_at, stage,
             consecutive_again, review_log_id, datetime.utcnow().isoformat()),
        )
        conn.commit()


def load_undo_snapshot(chat_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM undo_snapshots WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def clear_undo_snapshot(chat_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM undo_snapshots WHERE chat_id=?", (chat_id,))
        conn.commit()


def apply_undo(chat_id: int) -> bool:
    """Restores the card to its pre-review state and removes the logged review. Single-use."""
    snapshot = load_undo_snapshot(chat_id)
    if snapshot is None:
        return False
    with get_connection() as conn:
        conn.execute(
            "UPDATE cards SET ease_factor=?, interval_days=?, repetitions=?, due_at=?, stage=?,"
            " consecutive_again=? WHERE id=?",
            (snapshot["ease_factor"], snapshot["interval_days"], snapshot["repetitions"],
             snapshot["due_at"], snapshot["stage"], snapshot["consecutive_again"], snapshot["card_id"]),
        )
        if snapshot["review_log_id"] is not None:
            conn.execute("DELETE FROM review_log WHERE id=?", (snapshot["review_log_id"],))
        conn.execute("DELETE FROM undo_snapshots WHERE chat_id=?", (chat_id,))
        conn.commit()
    return True


def export_backup(chat_id: int) -> str:
    """Dumps all cards and chat settings for this chat as a JSON string."""
    with get_connection() as conn:
        cards = [dict(r) for r in conn.execute("SELECT * FROM cards WHERE chat_id=?", (chat_id,))]
        settings = [
            dict(r) for r in conn.execute(
                "SELECT key, value FROM chat_settings WHERE chat_id=?", (chat_id,)
            )
        ]
    return json.dumps(
        {"exported_at": datetime.utcnow().isoformat(), "cards": cards, "settings": settings},
        indent=2,
    )
