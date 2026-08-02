import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "cards.db"
_IST = timedelta(hours=5, minutes=30)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now_ist() -> datetime:
    return datetime.utcnow() + _IST


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
        for col, typedef in [
            ("tags", "TEXT DEFAULT NULL"),
            ("ease_factor", "REAL NOT NULL DEFAULT 2.5"),
            ("interval_days", "INTEGER NOT NULL DEFAULT 1"),
            ("repetitions", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def _sm2(ease_factor: float, interval_days: int, repetitions: int, quality: int):
    """SM-2 algorithm. quality: 1=Again, 3=Hard, 4=Good, 5=Easy."""
    new_ef = max(1.3, ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if quality < 3:
        return new_ef, 1, 0
    if repetitions == 0:
        new_interval = 1
    elif repetitions == 1:
        new_interval = 6
    else:
        new_interval = round(interval_days * ease_factor)
    return new_ef, max(1, new_interval), repetitions + 1


def add_card(question: str, answer: str, chat_id: int, tags: Optional[str] = None) -> int:
    now = datetime.utcnow()
    due_at = (now + timedelta(days=1)).isoformat()
    with _get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags,"
            " ease_factor, interval_days, repetitions) VALUES (?,?,0,?,?,?,?,2.5,1,0)",
            (question, answer, due_at, now.isoformat(), chat_id, tags),
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
    with _get_connection() as conn:
        for card in cards:
            cursor = conn.execute(
                "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id, tags,"
                " ease_factor, interval_days, repetitions) VALUES (?,?,0,?,?,?,?,2.5,1,0)",
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
    with _get_connection() as conn:
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
    with _get_connection() as conn:
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
    with _get_connection() as conn:
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
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id=? AND (question LIKE ? OR answer LIKE ?)",
            (chat_id, pattern, pattern),
        ).fetchall()
        return [dict(row) for row in rows]


def record_answer(card_id: int, quality: int) -> None:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT ease_factor, interval_days, repetitions FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        if row is None:
            return
        new_ef, new_interval, new_reps = _sm2(
            row["ease_factor"], row["interval_days"], row["repetitions"], quality
        )
        due_at = (datetime.utcnow() + timedelta(days=new_interval)).isoformat()
        conn.execute(
            "UPDATE cards SET ease_factor=?, interval_days=?, repetitions=?, due_at=?, stage=?"
            " WHERE id=?",
            (new_ef, new_interval, new_reps, due_at, new_reps, card_id),
        )
        conn.commit()


def log_review(chat_id: int, card_id: int, quality: int) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO review_log (card_id, chat_id, quality, reviewed_at) VALUES (?,?,?,?)",
            (card_id, chat_id, quality, datetime.utcnow().isoformat()),
        )
        conn.commit()


def snooze_card(card_id: int, delta: timedelta) -> None:
    new_due = (datetime.utcnow() + delta).isoformat()
    with _get_connection() as conn:
        conn.execute("UPDATE cards SET due_at=? WHERE id=?", (new_due, card_id))
        conn.commit()


def get_card(card_id: int) -> Optional[dict]:
    with _get_connection() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return dict(row) if row else None


def get_registered_chat_id() -> Optional[int]:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='chat_id'"
        ).fetchone()
        return int(row["value"]) if row else None


def set_registered_chat_id(chat_id: int) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)",
            (str(chat_id),),
        )
        conn.commit()


def get_global_setting(key: str) -> Optional[str]:
    with _get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_global_setting(key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value)
        )
        conn.commit()


def get_setting(chat_id: int, key: str) -> Optional[str]:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key)
        ).fetchone()
        return row["value"] if row else None


def set_setting(chat_id: int, key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, key, value) VALUES (?,?,?)",
            (chat_id, key, value),
        )
        conn.commit()


def set_daily_goal(chat_id: int, goal: int) -> None:
    set_setting(chat_id, "daily_goal", str(goal))


def update_streak(chat_id: int) -> None:
    today = _now_ist().date().isoformat()
    yesterday = (_now_ist() - timedelta(days=1)).date().isoformat()
    last = get_setting(chat_id, "streak_last_date")
    if last == today:
        return
    current = int(get_setting(chat_id, "streak_current") or "0")
    longest = int(get_setting(chat_id, "streak_longest") or "0")
    current = current + 1 if last == yesterday else 1
    longest = max(longest, current)
    set_setting(chat_id, "streak_last_date", today)
    set_setting(chat_id, "streak_current", str(current))
    set_setting(chat_id, "streak_longest", str(longest))


def get_streak_info(chat_id: int) -> dict:
    return {
        "current": int(get_setting(chat_id, "streak_current") or "0"),
        "longest": int(get_setting(chat_id, "streak_longest") or "0"),
        "last_date": get_setting(chat_id, "streak_last_date"),
    }


def get_weekly_stats(chat_id: int) -> dict:
    since = (datetime.utcnow() - timedelta(days=7)).isoformat()
    with _get_connection() as conn:
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
