import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from studybot.db.connection import get_connection

IST = timedelta(hours=5, minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def log_review(user_id: int, card_id: int, quality: int) -> int:
    with get_connection() as conn:
        return conn.execute(
            text(
                "INSERT INTO review_log (user_id, card_id, quality, reviewed_at)"
                " VALUES (:uid, :cid, :q, :at) RETURNING id"
            ),
            {"uid": user_id, "cid": card_id, "q": quality, "at": _now()},
        ).scalar_one()


def delete_review_log_entry(user_id: int, log_id: int) -> None:
    with get_connection() as conn:
        conn.execute(text("DELETE FROM review_log WHERE id=:id AND user_id=:uid"),
                     {"id": log_id, "uid": user_id})


def get_card_history(user_id: int, card_id: int, limit: int = 5) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT quality, reviewed_at FROM review_log WHERE card_id=:cid AND user_id=:uid"
                " ORDER BY reviewed_at DESC LIMIT :lim"
            ),
            {"cid": card_id, "uid": user_id, "lim": limit},
        )
        return [dict(r._mapping) for r in rows]


def count_reviews_today(user_id: int) -> int:
    """Reviews logged so far on the current IST day — drives the daily cap."""
    today_start_ist = (_now() + IST).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_ist - IST
    with get_connection() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM review_log WHERE user_id=:uid AND reviewed_at>=:since"),
            {"uid": user_id, "since": today_start_utc},
        ).scalar_one()


def get_weekly_stats(user_id: int) -> dict:
    since = _now() - timedelta(days=7)
    with get_connection() as conn:
        rows = list(conn.execute(
            text("SELECT quality, reviewed_at FROM review_log WHERE user_id=:uid AND reviewed_at>=:since"),
            {"uid": user_id, "since": since},
        ))
    total = len(rows)
    correct = sum(1 for r in rows if r.quality >= 3)
    accuracy = round(correct / total * 100) if total else 0
    by_day: dict[str, int] = {}
    for r in rows:
        day = (r.reviewed_at + IST).date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
    return {"total": total, "correct": correct, "accuracy": accuracy, "active_days": len(by_day), "by_day": by_day}


def get_retention_stats(user_id: int, days: int = 30) -> dict:
    """True retention: how often you recalled a card you'd already learned.

    A card's very first review is excluded — it measures nothing about memory,
    only whether you happened to know the material already.
    """
    since = _now() - timedelta(days=days)
    with get_connection() as conn:
        rows = list(conn.execute(
            text(
                "SELECT r.card_id, r.quality, r.reviewed_at, c.tags"
                " FROM review_log r LEFT JOIN cards c ON c.id=r.card_id AND c.user_id=r.user_id"
                " WHERE r.user_id=:uid ORDER BY r.card_id, r.reviewed_at"
            ),
            {"uid": user_id},
        ))

    seen_first: set[int] = set()
    total = passed = 0
    per_tag: dict[str, list[int]] = {}
    for row in rows:
        if row.card_id not in seen_first:
            seen_first.add(row.card_id)
            continue  # skip each card's first-ever review
        if row.reviewed_at < since:
            continue
        total += 1
        ok = 1 if row.quality > 1 else 0
        passed += ok
        for tag in (row.tags or "").split(","):
            tag = tag.strip()
            if tag:
                per_tag.setdefault(tag, []).append(ok)

    by_tag = {
        tag: {"reviews": len(vals), "retention": round(sum(vals) / len(vals) * 100)}
        for tag, vals in per_tag.items() if vals
    }
    return {
        "days": days, "reviews": total,
        "retention": round(passed / total * 100) if total else 0,
        "by_tag": dict(sorted(by_tag.items(), key=lambda kv: kv[1]["retention"])),
    }


def save_undo_snapshot(user_id: int, card: dict, review_log_id: Optional[int]) -> None:
    """Store a card's pre-review state so the next /undo can restore it exactly."""
    with get_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO undo_snapshots (user_id, card_id, ease_factor, interval_days, repetitions,"
                " due_at, stage, consecutive_again, review_log_id, created_at, stability, difficulty, last_review)"
                " VALUES (:uid, :cid, :ef, :iv, :rep, :due, :stage, :ca, :log, :created, :stab, :diff, :lr)"
                " ON CONFLICT (user_id) DO UPDATE SET card_id=:cid, ease_factor=:ef, interval_days=:iv,"
                " repetitions=:rep, due_at=:due, stage=:stage, consecutive_again=:ca, review_log_id=:log,"
                " created_at=:created, stability=:stab, difficulty=:diff, last_review=:lr"
            ),
            {
                "uid": user_id, "cid": card["id"], "ef": card["ease_factor"], "iv": card["interval_days"],
                "rep": card["repetitions"], "due": card["due_at"], "stage": card["stage"],
                "ca": card["consecutive_again"], "log": review_log_id, "created": _now(),
                "stab": card.get("stability"), "diff": card.get("difficulty"), "lr": card.get("last_review"),
            },
        )


def load_undo_snapshot(user_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM undo_snapshots WHERE user_id=:uid"), {"uid": user_id}
        ).first()
        return dict(row._mapping) if row else None


def clear_undo_snapshot(user_id: int) -> None:
    with get_connection() as conn:
        conn.execute(text("DELETE FROM undo_snapshots WHERE user_id=:uid"), {"uid": user_id})


def apply_undo(user_id: int) -> bool:
    """Restores the card to its pre-review state and removes the logged review. Single-use."""
    snapshot = load_undo_snapshot(user_id)
    if snapshot is None:
        return False
    with get_connection() as conn:
        conn.execute(
            text(
                "UPDATE cards SET ease_factor=:ef, interval_days=:iv, repetitions=:rep, due_at=:due,"
                " stage=:stage, consecutive_again=:ca, stability=:stab, difficulty=:diff, last_review=:lr"
                " WHERE id=:cid AND user_id=:uid"
            ),
            {
                "ef": snapshot["ease_factor"], "iv": snapshot["interval_days"], "rep": snapshot["repetitions"],
                "due": snapshot["due_at"], "stage": snapshot["stage"], "ca": snapshot["consecutive_again"],
                "stab": snapshot["stability"], "diff": snapshot["difficulty"], "lr": snapshot["last_review"],
                "cid": snapshot["card_id"], "uid": user_id,
            },
        )
        if snapshot["review_log_id"] is not None:
            conn.execute(text("DELETE FROM review_log WHERE id=:id AND user_id=:uid"),
                         {"id": snapshot["review_log_id"], "uid": user_id})
        conn.execute(text("DELETE FROM undo_snapshots WHERE user_id=:uid"), {"uid": user_id})
    return True


def export_backup(user_id: int) -> str:
    """Dumps all cards and settings for this user as a JSON string."""
    with get_connection() as conn:
        cards = [dict(r._mapping) for r in conn.execute(
            text("SELECT * FROM cards WHERE user_id=:uid"), {"uid": user_id}
        )]
        settings = [dict(r._mapping) for r in conn.execute(
            text("SELECT key, value FROM user_settings WHERE user_id=:uid"), {"uid": user_id}
        )]

    def _default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"not JSON serializable: {o!r}")

    return json.dumps(
        {"exported_at": _now().isoformat(), "cards": cards, "settings": settings},
        indent=2, default=_default,
    )
