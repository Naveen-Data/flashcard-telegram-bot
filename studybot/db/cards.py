from datetime import datetime, timedelta
from typing import Optional

from studybot.db.connection import get_connection
from studybot.sm2 import LEECH_THRESHOLD, sm2


def add_card(
    question: str, answer: str, chat_id: int, tags: Optional[str] = None,
    card_type: str = "basic", image_file_id: Optional[str] = None,
) -> int:
    now = datetime.utcnow()
    due_at = (now + timedelta(days=1)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags,"
            " ease_factor, interval_days, repetitions, card_type, image_file_id)"
            " VALUES (?,?,0,?,?,?,?,2.5,1,0,?,?)",
            (question, answer, due_at, now.isoformat(), chat_id, tags, card_type, image_file_id),
        )
        conn.commit()
        return cursor.lastrowid


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
                " ease_factor, interval_days, repetitions) VALUES (?,?,0,?,?,?,?,2.5,1,0)",
                (card["question"], card["answer"], due_at, now_iso, chat_id, card.get("tags")),
            )
            ids.append(cursor.lastrowid)
        conn.commit()
    return ids


def edit_card(
    card_id: int, question: Optional[str] = None, answer: Optional[str] = None,
    tags: Optional[str] = None,
) -> bool:
    """Update whichever fields are provided. Returns False if the card doesn't exist."""
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            return False
        updates: list[str] = []
        params: list = []
        if question is not None:
            updates.append("question=?")
            params.append(question)
        if answer is not None:
            updates.append("answer=?")
            params.append(answer)
        if tags is not None:
            updates.append("tags=?")
            params.append(tags)
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


def list_due_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        if tag:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id=? AND due_at<=?"
                " AND ',' || tags || ',' LIKE ?",
                (chat_id, now, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id=? AND due_at<=?",
                (chat_id, now),
            ).fetchall()
        return [dict(row) for row in rows]


def list_all_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    with get_connection() as conn:
        if tag:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id=? AND ',' || tags || ',' LIKE ?",
                (chat_id, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id=?", (chat_id,)
            ).fetchall()
        return [dict(row) for row in rows]


def list_tags(chat_id: int) -> list[tuple[str, int, int]]:
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT tags, due_at FROM cards WHERE chat_id=? AND tags IS NOT NULL",
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
    with get_connection() as conn:
        total: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id=?{tag_filter}",
            (chat_id,) + tag_params,
        ).fetchone()[0]
        due: int = conn.execute(
            f"SELECT COUNT(*) FROM cards WHERE chat_id=? AND due_at<=?{tag_filter}",
            (chat_id, now) + tag_params,
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT stage, COUNT(*) AS count FROM cards WHERE chat_id=?{tag_filter} GROUP BY stage",
            (chat_id,) + tag_params,
        ).fetchall()
        by_stage: dict[int, int] = {row["stage"]: row["count"] for row in rows}
    return {"total": total, "due": due, "by_stage": by_stage}


def search_cards(chat_id: int, keyword: str) -> list[dict]:
    pattern = f"%{keyword}%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND (question LIKE ? OR answer LIKE ?)",
            (chat_id, pattern, pattern),
        ).fetchall()
        return [dict(row) for row in rows]


def record_answer(card_id: int, quality: int) -> None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT ease_factor, interval_days, repetitions, consecutive_again FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        if row is None:
            return
        new_ef, new_interval, new_reps = sm2(
            row["ease_factor"], row["interval_days"], row["repetitions"], quality
        )
        new_consecutive_again = row["consecutive_again"] + 1 if quality == 1 else 0
        due_at = (datetime.utcnow() + timedelta(days=new_interval)).isoformat()
        conn.execute(
            "UPDATE cards SET ease_factor=?, interval_days=?, repetitions=?, due_at=?, stage=?,"
            " consecutive_again=? WHERE id=?",
            (new_ef, new_interval, new_reps, due_at, new_reps, new_consecutive_again, card_id),
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
            "SELECT * FROM cards WHERE chat_id=? AND consecutive_again>=? ORDER BY consecutive_again DESC",
            (chat_id, LEECH_THRESHOLD),
        ).fetchall()
        return [dict(row) for row in rows]


def list_weak_cards(chat_id: int, limit: int = 10) -> list[dict]:
    """Cards that are either leeches or have a low ease_factor — good candidates to re-teach."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND (consecutive_again>=? OR ease_factor<=1.6)"
            " ORDER BY consecutive_again DESC, ease_factor ASC LIMIT ?",
            (chat_id, LEECH_THRESHOLD, limit),
        ).fetchall()
        return [dict(row) for row in rows]
