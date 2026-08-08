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


def count_reviews_today(chat_id: int) -> int:
    """Reviews logged so far on the current IST day — drives the daily cap."""
    today_start_ist = ((datetime.utcnow() + _IST).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - _IST).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM review_log WHERE chat_id=? AND reviewed_at>=?",
            (chat_id, today_start_ist),
        ).fetchone()
    return row[0] if row else 0


def get_retention_stats(chat_id: int, days: int = 30) -> dict:
    """True retention: how often you recalled a card you'd already learned.

    A card's very first review is excluded — it measures nothing about memory,
    only whether you happened to know the material already.
    """
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT r.card_id, r.quality, r.reviewed_at, c.tags"
            " FROM review_log r LEFT JOIN cards c ON c.id=r.card_id"
            " WHERE r.chat_id=? ORDER BY r.card_id, r.reviewed_at",
            (chat_id,),
        ).fetchall()

    seen_first: set[int] = set()
    total = passed = 0
    per_tag: dict[str, list[int]] = {}
    for row in rows:
        card_id = row["card_id"]
        if card_id not in seen_first:
            seen_first.add(card_id)
            continue  # skip each card's first-ever review
        if row["reviewed_at"] < since:
            continue
        total += 1
        ok = 1 if row["quality"] > 1 else 0
        passed += ok
        for tag in (row["tags"] or "").split(","):
            tag = tag.strip()
            if tag:
                per_tag.setdefault(tag, []).append(ok)

    by_tag = {
        tag: {"reviews": len(vals), "retention": round(sum(vals) / len(vals) * 100)}
        for tag, vals in per_tag.items()
        if vals
    }
    return {
        "days": days,
        "reviews": total,
        "retention": round(passed / total * 100) if total else 0,
        "by_tag": dict(sorted(by_tag.items(), key=lambda kv: kv[1]["retention"])),
    }


def save_undo_snapshot(chat_id: int, card: dict, review_log_id: Optional[int]) -> None:
    """Store a card's pre-review state so the next /undo can restore it exactly."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO undo_snapshots"
            " (chat_id, card_id, ease_factor, interval_days, repetitions, due_at, stage,"
            "  consecutive_again, review_log_id, created_at, stability, difficulty, last_review)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, card["id"], card["ease_factor"], card["interval_days"],
             card["repetitions"], card["due_at"], card["stage"], card["consecutive_again"],
             review_log_id, datetime.utcnow().isoformat(),
             card.get("stability"), card.get("difficulty"), card.get("last_review")),
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
            " consecutive_again=?, stability=?, difficulty=?, last_review=? WHERE id=?",
            (snapshot["ease_factor"], snapshot["interval_days"], snapshot["repetitions"],
             snapshot["due_at"], snapshot["stage"], snapshot["consecutive_again"],
             snapshot["stability"], snapshot["difficulty"], snapshot["last_review"],
             snapshot["card_id"]),
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
