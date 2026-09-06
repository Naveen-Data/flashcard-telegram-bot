"""Card CRUD, review scheduling, and deck queries. Every function takes a
`user_id` and every query is ownership-scoped in SQL — never fetch by a
global card ID and check ownership afterwards in Python.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from studybot import fsrs, scheduling
from studybot.db.connection import get_connection
from studybot.fsrs import LEECH_THRESHOLD

_ACTIVE = "suspended = FALSE AND (buried_until IS NULL OR buried_until <= :now)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canon_tags(tags: Optional[str]) -> Optional[str]:
    """Trim/lowercase/dedupe so PostgreSQL array-containment matching is exact."""
    if not tags:
        return None
    seen: list[str] = []
    for t in tags.split(","):
        t = t.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return ",".join(seen) if seen else None


def _row(row) -> Optional[dict]:
    return dict(row._mapping) if row is not None else None


def add_card(
    question: str, answer: str, user_id: int, tags: Optional[str] = None,
    card_type: str = "basic", image_file_id: Optional[str] = None,
    notes: Optional[str] = None, reverse_of: Optional[int] = None,
) -> int:
    now = _now()
    with get_connection() as conn:
        return conn.execute(
            text(
                "INSERT INTO cards (question, answer, stage, due_at, created_at, user_id, tags,"
                " ease_factor, interval_days, repetitions, card_type, image_file_id, notes, reverse_of)"
                " VALUES (:q, :a, 0, :due, :created, :uid, :tags, 2.5, 1, 0, :ctype, :img, :notes, :rev)"
                " RETURNING id"
            ),
            {
                "q": question, "a": answer, "due": now + timedelta(days=1), "created": now,
                "uid": user_id, "tags": _canon_tags(tags), "ctype": card_type,
                "img": image_file_id, "notes": notes, "rev": reverse_of,
            },
        ).scalar_one()


def add_card_with_reverse(
    question: str, answer: str, user_id: int, tags: Optional[str] = None,
    notes: Optional[str] = None,
) -> tuple[int, int]:
    """Create a card plus its mirror (answer->question), scheduled independently."""
    forward_id = add_card(question, answer, user_id, tags=tags, notes=notes)
    reverse_id = add_card(answer, question, user_id, tags=tags, notes=notes, reverse_of=forward_id)
    return forward_id, reverse_id


def add_cards_bulk(cards: list[dict], user_id: int) -> list[int]:
    if not cards:
        return []
    now = _now()
    due = now + timedelta(days=1)
    ids: list[int] = []
    with get_connection() as conn:
        for card in cards:
            card_id = conn.execute(
                text(
                    "INSERT INTO cards (question, answer, stage, due_at, created_at, user_id, tags,"
                    " ease_factor, interval_days, repetitions, notes)"
                    " VALUES (:q, :a, 0, :due, :created, :uid, :tags, 2.5, 1, 0, :notes) RETURNING id"
                ),
                {
                    "q": card["question"], "a": card["answer"], "due": due, "created": now,
                    "uid": user_id, "tags": _canon_tags(card.get("tags")), "notes": card.get("notes"),
                },
            ).scalar_one()
            ids.append(card_id)
    return ids


def edit_card(
    user_id: int, card_id: int, question: Optional[str] = None, answer: Optional[str] = None,
    tags: Optional[str] = None, notes: Optional[str] = None,
) -> bool:
    """Update whichever fields are provided. Returns False if the card doesn't exist or isn't owned."""
    updates: list[str] = []
    params: dict = {"uid": user_id, "id": card_id}
    for column, value, transform in (
        ("question", question, None), ("answer", answer, None),
        ("tags", tags, _canon_tags), ("notes", notes, None),
    ):
        if value is not None:
            updates.append(f"{column} = :{column}")
            params[column] = transform(value) if transform else value
    if not updates:
        with get_connection() as conn:
            return conn.execute(
                text("SELECT 1 FROM cards WHERE id=:id AND user_id=:uid"), params
            ).first() is not None
    with get_connection() as conn:
        result = conn.execute(
            text(f"UPDATE cards SET {', '.join(updates)} WHERE id=:id AND user_id=:uid"), params
        )
        return result.rowcount > 0


def delete_card(user_id: int, card_id: int) -> bool:
    with get_connection() as conn:
        conn.execute(text("DELETE FROM review_log WHERE card_id=:id AND user_id=:uid"),
                     {"id": card_id, "uid": user_id})
        result = conn.execute(text("DELETE FROM cards WHERE id=:id AND user_id=:uid"),
                               {"id": card_id, "uid": user_id})
        return result.rowcount > 0


def set_suspended(user_id: int, card_id: int, suspended: bool) -> bool:
    with get_connection() as conn:
        result = conn.execute(
            text("UPDATE cards SET suspended=:s WHERE id=:id AND user_id=:uid"),
            {"s": suspended, "id": card_id, "uid": user_id},
        )
        return result.rowcount > 0


def bury_card(user_id: int, card_id: int) -> bool:
    """Hide a card until the next IST midnight without touching its schedule."""
    with get_connection() as conn:
        result = conn.execute(
            text("UPDATE cards SET buried_until=:until WHERE id=:id AND user_id=:uid"),
            {"until": scheduling.next_ist_midnight_utc(), "id": card_id, "uid": user_id},
        )
        return result.rowcount > 0


def list_suspended(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM cards WHERE user_id=:uid AND suspended=TRUE ORDER BY id"),
            {"uid": user_id},
        )
        return [dict(r._mapping) for r in rows]


def list_due_cards(user_id: int, tag: Optional[str] = None) -> list[dict]:
    now = _now()
    with get_connection() as conn:
        if tag:
            rows = conn.execute(
                text(
                    f"SELECT * FROM cards WHERE user_id=:uid AND due_at<=:now AND {_ACTIVE}"
                    " AND string_to_array(tags, ',') @> ARRAY[:tag] ORDER BY due_at"
                ),
                {"uid": user_id, "now": now, "tag": tag.strip().lower()},
            )
        else:
            rows = conn.execute(
                text(f"SELECT * FROM cards WHERE user_id=:uid AND due_at<=:now AND {_ACTIVE} ORDER BY due_at"),
                {"uid": user_id, "now": now},
            )
        return [dict(r._mapping) for r in rows]


def list_all_cards(user_id: int, tag: Optional[str] = None) -> list[dict]:
    now = _now()
    with get_connection() as conn:
        if tag:
            rows = conn.execute(
                text(
                    f"SELECT * FROM cards WHERE user_id=:uid AND {_ACTIVE}"
                    " AND string_to_array(tags, ',') @> ARRAY[:tag] ORDER BY due_at"
                ),
                {"uid": user_id, "now": now, "tag": tag.strip().lower()},
            )
        else:
            rows = conn.execute(
                text(f"SELECT * FROM cards WHERE user_id=:uid AND {_ACTIVE} ORDER BY due_at"),
                {"uid": user_id, "now": now},
            )
        return [dict(r._mapping) for r in rows]


def list_tags(user_id: int) -> list[tuple[str, int, int]]:
    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            text(f"SELECT tags, due_at FROM cards WHERE user_id=:uid AND tags IS NOT NULL AND {_ACTIVE}"),
            {"uid": user_id, "now": now},
        )
        rows = list(rows)
    tag_totals: dict[str, int] = {}
    tag_due: dict[str, int] = {}
    for row in rows:
        for tag in row.tags.split(","):
            tag = tag.strip()
            if not tag:
                continue
            tag_totals[tag] = tag_totals.get(tag, 0) + 1
            if row.due_at <= now:
                tag_due[tag] = tag_due.get(tag, 0) + 1
    return sorted(
        [(tag, total, tag_due.get(tag, 0)) for tag, total in tag_totals.items()],
        key=lambda x: x[0],
    )


def get_stats(user_id: int, tag: Optional[str] = None) -> dict:
    now = _now()
    tag_filter = " AND string_to_array(tags, ',') @> ARRAY[:tag]" if tag else ""
    params: dict = {"uid": user_id, "now": now}
    if tag:
        params["tag"] = tag.strip().lower()
    with get_connection() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM cards WHERE user_id=:uid{tag_filter}"), params
        ).scalar_one()
        due = conn.execute(
            text(f"SELECT COUNT(*) FROM cards WHERE user_id=:uid AND due_at<=:now AND {_ACTIVE}{tag_filter}"),
            params,
        ).scalar_one()
        rows = conn.execute(
            text(f"SELECT stage, COUNT(*) AS count FROM cards WHERE user_id=:uid{tag_filter} GROUP BY stage"),
            params,
        )
        by_stage = {r.stage: r.count for r in rows}
        suspended = conn.execute(
            text(f"SELECT COUNT(*) FROM cards WHERE user_id=:uid AND suspended=TRUE{tag_filter}"), params
        ).scalar_one()
    return {"total": total, "due": due, "by_stage": by_stage, "suspended": suspended}


def get_forecast(user_id: int, days: int = 7) -> list[tuple[str, int]]:
    """Due-card counts per IST day for the next `days` days, plus an overdue bucket."""
    now = _now()
    with get_connection() as conn:
        rows = list(conn.execute(
            text(f"SELECT due_at FROM cards WHERE user_id=:uid AND {_ACTIVE}"), {"uid": user_id, "now": now}
        ))

    today_ist = (now + scheduling.IST).date()
    buckets: dict[str, int] = {}
    overdue = 0
    for row in rows:
        due_ist = (row.due_at + scheduling.IST).date()
        delta = (due_ist - today_ist).days
        if delta < 0:
            overdue += 1
        elif delta < days:
            buckets[due_ist.isoformat()] = buckets.get(due_ist.isoformat(), 0) + 1

    result: list[tuple[str, int]] = [("Overdue", overdue)]
    for offset in range(days):
        day = today_ist + timedelta(days=offset)
        label = "Today" if offset == 0 else ("Tomorrow" if offset == 1 else day.strftime("%a %d %b"))
        result.append((label, buckets.get(day.isoformat(), 0)))
    return result


def search_cards(user_id: int, keyword: str) -> list[dict]:
    pattern = f"%{keyword}%"
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM cards WHERE user_id=:uid AND (question ILIKE :p OR answer ILIKE :p)"),
            {"uid": user_id, "p": pattern},
        )
        return [dict(r._mapping) for r in rows]


def record_answer(
    user_id: int, card_id: int, quality: int, desired_retention: float = fsrs.DEFAULT_RETENTION,
    study_window: Optional[str] = None, exam_date: Optional[str] = None,
) -> Optional[dict]:
    """Advance a card through FSRS and write back its new memory state and due date."""
    now = _now()
    with get_connection() as conn:
        row = conn.execute(
            text(
                "SELECT stability, difficulty, last_review, repetitions, consecutive_again"
                " FROM cards WHERE id=:id AND user_id=:uid"
            ),
            {"id": card_id, "uid": user_id},
        ).mappings().first()
        if row is None:
            return None

        if row["last_review"]:
            elapsed = max(0.0, (now - row["last_review"]).total_seconds() / 86400)
        else:
            elapsed = 0.0

        stability, difficulty, interval = fsrs.schedule(
            row["stability"], row["difficulty"], elapsed, quality, desired_retention=desired_retention,
        )
        interval = scheduling.clamp_interval_for_exam(interval, exam_date, now)

        due_at = now + timedelta(days=interval)
        due_at = scheduling.apply_study_window(due_at, study_window, card_id)

        repetitions = 0 if quality == 1 else (row["repetitions"] or 0) + 1
        consecutive_again = (row["consecutive_again"] or 0) + 1 if quality == 1 else 0

        conn.execute(
            text(
                "UPDATE cards SET stability=:s, difficulty=:d, last_review=:lr, due_at=:due,"
                " interval_days=:iv, repetitions=:rep, stage=:stage, consecutive_again=:ca,"
                " buried_until=NULL WHERE id=:id AND user_id=:uid"
            ),
            {
                "s": stability, "d": difficulty, "lr": now, "due": due_at, "iv": interval,
                "rep": repetitions, "stage": repetitions, "ca": consecutive_again,
                "id": card_id, "uid": user_id,
            },
        )
        return _row(conn.execute(
            text("SELECT * FROM cards WHERE id=:id AND user_id=:uid"), {"id": card_id, "uid": user_id}
        ).first())


def snooze_card(user_id: int, card_id: int, delta: timedelta) -> bool:
    with get_connection() as conn:
        result = conn.execute(
            text("UPDATE cards SET due_at=:due WHERE id=:id AND user_id=:uid"),
            {"due": _now() + delta, "id": card_id, "uid": user_id},
        )
        return result.rowcount > 0


def get_card(user_id: int, card_id: int) -> Optional[dict]:
    with get_connection() as conn:
        return _row(conn.execute(
            text("SELECT * FROM cards WHERE id=:id AND user_id=:uid"), {"id": card_id, "uid": user_id}
        ).first())


def list_leeches(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM cards WHERE user_id=:uid AND consecutive_again>=:t"
                " ORDER BY consecutive_again DESC"
            ),
            {"uid": user_id, "t": LEECH_THRESHOLD},
        )
        return [dict(r._mapping) for r in rows]


def list_weak_cards(user_id: int, limit: int = 10) -> list[dict]:
    """Leeches, or cards FSRS rates as intrinsically hard — worth re-teaching."""
    with get_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM cards WHERE user_id=:uid AND (consecutive_again>=:t OR difficulty>=7.5)"
                " ORDER BY consecutive_again DESC, difficulty DESC LIMIT :lim"
            ),
            {"uid": user_id, "t": LEECH_THRESHOLD, "lim": limit},
        )
        return [dict(r._mapping) for r in rows]
