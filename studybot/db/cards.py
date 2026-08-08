from datetime import datetime, timedelta
from typing import Optional

from studybot import fsrs, scheduling
from studybot.db.connection import get_connection
from studybot.fsrs import LEECH_THRESHOLD

# Cards hidden from review: explicitly suspended, or buried until later today.
_ACTIVE = "suspended=0 AND (buried_until IS NULL OR buried_until<=?)"


def _active_params(now_iso: str) -> tuple:
    return (now_iso,)


def add_card(
    question: str, answer: str, chat_id: int, tags: Optional[str] = None,
    card_type: str = "basic", image_file_id: Optional[str] = None,
    notes: Optional[str] = None, reverse_of: Optional[int] = None,
) -> int:
    now = datetime.utcnow()
    due_at = (now + timedelta(days=1)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags,"
            " ease_factor, interval_days, repetitions, card_type, image_file_id, notes, reverse_of)"
            " VALUES (?,?,0,?,?,?,?,2.5,1,0,?,?,?,?)",
            (question, answer, due_at, now.isoformat(), chat_id, tags, card_type,
             image_file_id, notes, reverse_of),
        )
        conn.commit()
        return cursor.lastrowid


def add_card_with_reverse(
    question: str, answer: str, chat_id: int, tags: Optional[str] = None,
    notes: Optional[str] = None,
) -> tuple[int, int]:
    """Create a card plus its mirror (answer->question), scheduled independently."""
    forward_id = add_card(question, answer, chat_id, tags=tags, notes=notes)
    reverse_id = add_card(answer, question, chat_id, tags=tags, notes=notes, reverse_of=forward_id)
    return forward_id, reverse_id


def add_cards_bulk(cards: list[dict], chat_id: int) -> list[int]:
    if not cards:
        return []
    now = datetime.utcnow()
    due_at = (now + timedelta(days=1)).isoformat()
    now_iso = now.isoformat()
    ids: list[int] = []
    with get_connection() as conn:
        for card in cards:
            cursor = conn.execute(
                "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags,"
                " ease_factor, interval_days, repetitions, notes) VALUES (?,?,0,?,?,?,?,2.5,1,0,?)",
                (card["question"], card["answer"], due_at, now_iso, chat_id,
                 card.get("tags"), card.get("notes")),
            )
            ids.append(cursor.lastrowid)
        conn.commit()
    return ids


def edit_card(
    card_id: int, question: Optional[str] = None, answer: Optional[str] = None,
    tags: Optional[str] = None, notes: Optional[str] = None,
) -> bool:
    """Update whichever fields are provided. Returns False if the card doesn't exist."""
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            return False
        updates: list[str] = []
        params: list = []
        for column, value in (
            ("question", question), ("answer", answer), ("tags", tags), ("notes", notes)
        ):
            if value is not None:
                updates.append(f"{column}=?")
                params.append(value)
        if updates:
            params.append(card_id)
            conn.execute(f"UPDATE cards SET {', '.join(updates)} WHERE id=?", params)
            conn.commit()
        return True


def delete_card(card_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
        conn.execute("DELETE FROM review_log WHERE card_id=?", (card_id,))
        conn.commit()
        return cursor.rowcount > 0


def set_suspended(card_id: int, suspended: bool) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE cards SET suspended=? WHERE id=?", (1 if suspended else 0, card_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def bury_card(card_id: int) -> bool:
    """Hide a card until the next IST midnight without touching its schedule."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE cards SET buried_until=? WHERE id=?",
            (scheduling.next_ist_midnight_utc().isoformat(), card_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_suspended(chat_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND suspended=1 ORDER BY id", (chat_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def list_due_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        if tag:
            rows = conn.execute(
                f"SELECT * FROM cards WHERE chat_id=? AND due_at<=? AND {_ACTIVE}"
                " AND ',' || tags || ',' LIKE ? ORDER BY due_at",
                (chat_id, now, now, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM cards WHERE chat_id=? AND due_at<=? AND {_ACTIVE} ORDER BY due_at",
                (chat_id, now, now),
            ).fetchall()
        return [dict(row) for row in rows]


def list_all_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        if tag:
            rows = conn.execute(
                f"SELECT * FROM cards WHERE chat_id=? AND {_ACTIVE}"
                " AND ',' || tags || ',' LIKE ? ORDER BY due_at",
                (chat_id, now, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM cards WHERE chat_id=? AND {_ACTIVE} ORDER BY due_at",
                (chat_id, now),
            ).fetchall()
        return [dict(row) for row in rows]


def list_tags(chat_id: int) -> list[tuple[str, int, int]]:
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT tags, due_at FROM cards WHERE chat_id=? AND tags IS NOT NULL AND {_ACTIVE}",
            (chat_id, now),
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
    with get_connection() as conn:
        total: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id=?{tag_filter}",
            (chat_id,) + tag_params,
        ).fetchone()[0]
        due: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id=? AND due_at<=? AND {_ACTIVE}{tag_filter}",
            (chat_id, now, now) + tag_params,
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT stage, COUNT(*) AS count FROM cards WHERE chat_id=?{tag_filter} GROUP BY stage",
            (chat_id,) + tag_params,
        ).fetchall()
        by_stage: dict[int, int] = {row["stage"]: row["count"] for row in rows}
        suspended: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id=? AND suspended=1{tag_filter}",
            (chat_id,) + tag_params,
        ).fetchone()[0]
    return {"total": total, "due": due, "by_stage": by_stage, "suspended": suspended}


def get_forecast(chat_id: int, days: int = 7) -> list[tuple[str, int]]:
    """Due-card counts per IST day for the next `days` days, plus an overdue bucket.

    Returned as [(label, count), ...] where the first entry is 'Overdue'.
    """
    now_utc = datetime.utcnow()
    now_iso = now_utc.isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT due_at FROM cards WHERE chat_id=? AND {_ACTIVE}", (chat_id, now_iso)
        ).fetchall()

    today_ist = (now_utc + scheduling.IST).date()
    buckets: dict[str, int] = {}
    overdue = 0
    for row in rows:
        try:
            due_ist = (datetime.fromisoformat(row["due_at"]) + scheduling.IST).date()
        except (ValueError, TypeError):
            continue
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


def search_cards(chat_id: int, keyword: str) -> list[dict]:
    pattern = f"%{keyword}%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND (question LIKE ? OR answer LIKE ?)",
            (chat_id, pattern, pattern),
        ).fetchall()
        return [dict(row) for row in rows]


def record_answer(
    card_id: int, quality: int, desired_retention: float = fsrs.DEFAULT_RETENTION,
    study_window: Optional[str] = None, exam_date: Optional[str] = None,
) -> None:
    """Advance a card through FSRS and write back its new memory state and due date."""
    now = datetime.utcnow()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT stability, difficulty, last_review, repetitions, consecutive_again"
            " FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        if row is None:
            return

        if row["last_review"]:
            try:
                elapsed = (now - datetime.fromisoformat(row["last_review"])).total_seconds() / 86400
            except (ValueError, TypeError):
                elapsed = 0.0
        else:
            elapsed = 0.0

        stability, difficulty, interval = fsrs.schedule(
            row["stability"], row["difficulty"], max(0.0, elapsed), quality,
            desired_retention=desired_retention,
        )
        interval = scheduling.clamp_interval_for_exam(interval, exam_date, now)

        due_at = now + timedelta(days=interval)
        due_at = scheduling.apply_study_window(due_at, study_window, card_id)

        repetitions = 0 if quality == 1 else (row["repetitions"] or 0) + 1
        consecutive_again = (row["consecutive_again"] or 0) + 1 if quality == 1 else 0

        conn.execute(
            "UPDATE cards SET stability=?, difficulty=?, last_review=?, due_at=?,"
            " interval_days=?, repetitions=?, stage=?, consecutive_again=?, buried_until=NULL"
            " WHERE id=?",
            (stability, difficulty, now.isoformat(), due_at.isoformat(), interval,
             repetitions, repetitions, consecutive_again, card_id),
        )
        conn.commit()


def snooze_card(card_id: int, delta: timedelta) -> None:
    new_due = (datetime.utcnow() + delta).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE cards SET due_at=? WHERE id=?", (new_due, card_id))
        conn.commit()


def get_card(card_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return dict(row) if row else None


def list_leeches(chat_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND consecutive_again>=?"
            " ORDER BY consecutive_again DESC",
            (chat_id, LEECH_THRESHOLD),
        ).fetchall()
        return [dict(row) for row in rows]


def list_weak_cards(chat_id: int, limit: int = 10) -> list[dict]:
    """Leeches, or cards FSRS rates as intrinsically hard — worth re-teaching."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND (consecutive_again>=? OR difficulty>=7.5)"
            " ORDER BY consecutive_again DESC, difficulty DESC LIMIT ?",
            (chat_id, LEECH_THRESHOLD, limit),
        ).fetchall()
        return [dict(row) for row in rows]
