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
            CREATE TABLE IF NOT EXISTS review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                card_id INTEGER NOT NULL,
                quality INTEGER NOT NULL,
                reviewed_at DATETIME NOT NULL
            )
        """)
        for col, definition in [
            ("tags", "TEXT DEFAULT NULL"),
            ("ease_factor", "REAL NOT NULL DEFAULT 2.5"),
            ("interval_days", "INTEGER NOT NULL DEFAULT 1"),
            ("repetitions", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with _get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def log_review(chat_id: int, card_id: int, quality: int) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO review_log (chat_id, card_id, quality, reviewed_at) VALUES (?, ?, ?, ?)",
            (chat_id, card_id, quality, datetime.utcnow().isoformat()),
        )
        conn.commit()


def update_streak(chat_id: int) -> dict:
    """Call once per card answered. Returns streak info and whether goal was just hit."""
    today = datetime.utcnow().date().isoformat()
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    last_date = get_setting(f"last_review_date:{chat_id}")
    streak = int(get_setting(f"streak:{chat_id}", "0"))
    longest = int(get_setting(f"longest_streak:{chat_id}", "0"))

    if last_date != today:
        streak = streak + 1 if last_date == yesterday else 1
        set_setting(f"last_review_date:{chat_id}", today)
        set_setting(f"cards_today:{chat_id}", "0")
        longest = max(streak, longest)
        set_setting(f"streak:{chat_id}", str(streak))
        set_setting(f"longest_streak:{chat_id}", str(longest))

    cards_today = int(get_setting(f"cards_today:{chat_id}", "0")) + 1
    set_setting(f"cards_today:{chat_id}", str(cards_today))
    goal = int(get_setting(f"daily_goal:{chat_id}", "10"))
    return {"streak": streak, "longest": longest, "cards_today": cards_today, "goal": goal, "goal_hit": cards_today == goal}


def get_streak_info(chat_id: int) -> dict:
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    last_date = get_setting(f"last_review_date:{chat_id}")
    streak = int(get_setting(f"streak:{chat_id}", "0"))
    if last_date and last_date < yesterday:
        streak = 0
    return {
        "streak": streak,
        "longest": int(get_setting(f"longest_streak:{chat_id}", "0")),
        "cards_today": int(get_setting(f"cards_today:{chat_id}", "0")),
        "goal": int(get_setting(f"daily_goal:{chat_id}", "10")),
        "last_review": last_date,
    }


def set_daily_goal(chat_id: int, goal: int) -> None:
    set_setting(f"daily_goal:{chat_id}", str(goal))


def search_cards(chat_id: int, query: str) -> list[dict]:
    pattern = f"%{query}%"
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id = ? AND (question LIKE ? OR answer LIKE ?)",
            (chat_id, pattern, pattern),
        ).fetchall()
        return [dict(row) for row in rows]


def get_weekly_stats(chat_id: int) -> dict:
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT quality, DATE(reviewed_at) as day FROM review_log"
            " WHERE chat_id = ? AND reviewed_at >= ?",
            (chat_id, week_ago),
        ).fetchall()
    total = len(rows)
    correct = sum(1 for r in rows if r["quality"] >= 3)
    by_day: dict[str, int] = {}
    for r in rows:
        by_day[r["day"]] = by_day.get(r["day"], 0) + 1
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total * 100) if total else 0,
        "by_day": by_day,
        "days_active": len(by_day),
    }


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


def list_all_cards(chat_id: int, tag: Optional[str] = None) -> list[dict]:
    with _get_connection() as conn:
        if tag:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id = ? AND ',' || tags || ',' LIKE ?",
                (chat_id, f"%,{tag},%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards WHERE chat_id = ?", (chat_id,)
            ).fetchall()
        return [dict(row) for row in rows]


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


def _sm2(ease_factor: float, interval: int, repetitions: int, quality: int) -> tuple[float, int, int]:
    """SM-2 algorithm. quality: 1=Again, 3=Hard, 4=Good, 5=Easy."""
    if quality >= 3:
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = max(1, round(interval * ease_factor))
        new_repetitions = repetitions + 1
        new_ef = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    else:
        new_interval = 1
        new_repetitions = 0
        new_ef = ease_factor - 0.2
    return max(1.3, round(new_ef, 2)), new_interval, new_repetitions


def record_answer(card_id: int, quality: int) -> None:
    """Record an answer using SM-2. quality: 1=Again, 3=Hard, 4=Good, 5=Easy."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT ease_factor, interval_days, repetitions FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            return
        new_ef, new_interval, new_reps = _sm2(
            row["ease_factor"], row["interval_days"], row["repetitions"], quality
        )
        due_at = (datetime.utcnow() + timedelta(days=new_interval)).isoformat()
        conn.execute(
            "UPDATE cards SET ease_factor=?, interval_days=?, repetitions=?, due_at=?, stage=? WHERE id=?",
            (new_ef, new_interval, new_reps, due_at, new_reps, card_id),
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
