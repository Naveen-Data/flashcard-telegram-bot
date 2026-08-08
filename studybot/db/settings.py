from datetime import datetime, timedelta
from typing import Optional

from studybot.db.connection import get_connection
from studybot.fsrs import DEFAULT_RETENTION

_IST = timedelta(hours=5, minutes=30)


def _now_ist() -> datetime:
    return datetime.utcnow() + _IST


def get_registered_chat_id() -> Optional[int]:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='chat_id'").fetchone()
        return int(row["value"]) if row else None


def set_registered_chat_id(chat_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)",
            (str(chat_id),),
        )
        conn.commit()


def get_global_setting(key: str) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_global_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value)
        )
        conn.commit()


def get_setting(chat_id: int, key: str) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key)
        ).fetchone()
        return row["value"] if row else None


def set_setting(chat_id: int, key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, key, value) VALUES (?,?,?)",
            (chat_id, key, value),
        )
        conn.commit()


def set_daily_goal(chat_id: int, goal: int) -> None:
    set_setting(chat_id, "daily_goal", str(goal))


def get_study_window(chat_id: int) -> Optional[str]:
    raw = get_setting(chat_id, "study_window")
    return raw or None


def set_study_window(chat_id: int, window: Optional[str]) -> None:
    set_setting(chat_id, "study_window", window or "")


def get_daily_cap(chat_id: int) -> Optional[int]:
    raw = get_setting(chat_id, "daily_cap")
    if not raw or not raw.isdigit() or int(raw) <= 0:
        return None
    return int(raw)


def set_daily_cap(chat_id: int, cap: Optional[int]) -> None:
    set_setting(chat_id, "daily_cap", str(cap) if cap else "")


def get_desired_retention(chat_id: int) -> float:
    raw = get_setting(chat_id, "desired_retention")
    try:
        value = float(raw) if raw else DEFAULT_RETENTION
    except ValueError:
        return DEFAULT_RETENTION
    return min(0.99, max(0.7, value))


def set_desired_retention(chat_id: int, retention: float) -> None:
    set_setting(chat_id, "desired_retention", str(round(retention, 3)))


def set_exam(chat_id: int, tag: str, date_str: str) -> None:
    set_setting(chat_id, f"exam:{tag}", date_str)


def clear_exam(chat_id: int, tag: str) -> None:
    set_setting(chat_id, f"exam:{tag}", "")


def list_exams(chat_id: int) -> list[tuple[str, str]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM chat_settings WHERE chat_id=? AND key LIKE 'exam:%'",
            (chat_id,),
        ).fetchall()
    return sorted(
        (row["key"].split(":", 1)[1], row["value"]) for row in rows if row["value"]
    )


def get_exam_date_for_card(chat_id: int, tags: Optional[str]) -> Optional[str]:
    """Soonest upcoming exam among a card's tags, if any."""
    if not tags:
        return None
    exams = dict(list_exams(chat_id))
    if not exams:
        return None
    dates = [exams[t.strip()] for t in tags.split(",") if t.strip() in exams]
    return min(dates) if dates else None


def get_dnd_window(chat_id: int) -> Optional[tuple[str, str]]:
    """Returns (start_hhmm, end_hhmm) in IST, or None if not set."""
    raw = get_setting(chat_id, "dnd_window")
    if not raw or "-" not in raw:
        return None
    start, end = raw.split("-", 1)
    return start, end


def set_dnd_window(chat_id: int, start_hhmm: Optional[str], end_hhmm: Optional[str]) -> None:
    if start_hhmm is None or end_hhmm is None:
        set_setting(chat_id, "dnd_window", "")
    else:
        set_setting(chat_id, "dnd_window", f"{start_hhmm}-{end_hhmm}")


def is_within_dnd(chat_id: int) -> bool:
    window = get_dnd_window(chat_id)
    if not window or not window[0]:
        return False
    start_str, end_str = window
    now = _now_ist().time()
    sh, sm = map(int, start_str.split(":"))
    eh, em = map(int, end_str.split(":"))
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # window wraps past midnight


def update_streak(chat_id: int) -> None:
    today = _now_ist().date().isoformat()
    yesterday = (_now_ist() - timedelta(days=1)).date().isoformat()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        def _get(key):
            row = conn.execute(
                "SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key)
            ).fetchone()
            return row["value"] if row else None

        def _set(key, value):
            conn.execute(
                "INSERT OR REPLACE INTO chat_settings (chat_id, key, value) VALUES (?,?,?)",
                (chat_id, key, value),
            )

        last = _get("streak_last_date")
        if last == today:
            conn.execute("ROLLBACK")
            return
        current = int(_get("streak_current") or "0")
        longest = int(_get("streak_longest") or "0")
        current = current + 1 if last == yesterday else 1
        longest = max(longest, current)
        _set("streak_last_date", today)
        _set("streak_current", str(current))
        _set("streak_longest", str(longest))
        conn.commit()


def get_streak_info(chat_id: int) -> dict:
    return {
        "current": int(get_setting(chat_id, "streak_current") or "0"),
        "longest": int(get_setting(chat_id, "streak_longest") or "0"),
        "last_date": get_setting(chat_id, "streak_last_date"),
    }
