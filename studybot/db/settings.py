"""Per-user settings (study window, cap, retention, exams, DND, streaks) and
app-wide config. There is no more single global "registered chat" — Telegram
identity now lives in studybot.db.users, resolved to a user_id per request.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from studybot.db.connection import get_connection
from studybot.fsrs import DEFAULT_RETENTION

IST = timedelta(hours=5, minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_ist() -> datetime:
    return _now() + IST


def get_global_setting(key: str) -> Optional[str]:
    with get_connection() as conn:
        return conn.execute(
            text("SELECT value FROM app_settings WHERE key=:k"), {"k": key}
        ).scalar()


def set_global_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value) VALUES (:k, :v)"
                " ON CONFLICT (key) DO UPDATE SET value=:v"
            ),
            {"k": key, "v": value},
        )


def get_setting(user_id: int, key: str) -> Optional[str]:
    with get_connection() as conn:
        return conn.execute(
            text("SELECT value FROM user_settings WHERE user_id=:u AND key=:k"),
            {"u": user_id, "k": key},
        ).scalar()


def set_setting(user_id: int, key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO user_settings (user_id, key, value) VALUES (:u, :k, :v)"
                " ON CONFLICT (user_id, key) DO UPDATE SET value=:v"
            ),
            {"u": user_id, "k": key, "v": value},
        )


def set_daily_goal(user_id: int, goal: int) -> None:
    set_setting(user_id, "daily_goal", str(goal))


def get_study_window(user_id: int) -> Optional[str]:
    return get_setting(user_id, "study_window") or None


def set_study_window(user_id: int, window: Optional[str]) -> None:
    set_setting(user_id, "study_window", window or "")


def get_daily_cap(user_id: int) -> Optional[int]:
    raw = get_setting(user_id, "daily_cap")
    if not raw or not raw.isdigit() or int(raw) <= 0:
        return None
    return int(raw)


def set_daily_cap(user_id: int, cap: Optional[int]) -> None:
    set_setting(user_id, "daily_cap", str(cap) if cap else "")


def get_desired_retention(user_id: int) -> float:
    raw = get_setting(user_id, "desired_retention")
    try:
        value = float(raw) if raw else DEFAULT_RETENTION
    except ValueError:
        return DEFAULT_RETENTION
    return min(0.99, max(0.7, value))


def set_desired_retention(user_id: int, retention: float) -> None:
    set_setting(user_id, "desired_retention", str(round(retention, 3)))


def set_exam(user_id: int, tag: str, date_str: str) -> None:
    set_setting(user_id, f"exam:{tag}", date_str)


def clear_exam(user_id: int, tag: str) -> None:
    set_setting(user_id, f"exam:{tag}", "")


def list_exams(user_id: int) -> list[tuple[str, str]]:
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT key, value FROM user_settings WHERE user_id=:u AND key LIKE 'exam:%'"),
            {"u": user_id},
        )
        rows = list(rows)
    return sorted((r.key.split(":", 1)[1], r.value) for r in rows if r.value)


def get_exam_date_for_card(user_id: int, tags: Optional[str]) -> Optional[str]:
    """Soonest upcoming exam among a card's tags, if any."""
    if not tags:
        return None
    exams = dict(list_exams(user_id))
    if not exams:
        return None
    dates = [exams[t.strip()] for t in tags.split(",") if t.strip() in exams]
    return min(dates) if dates else None


def get_dnd_window(user_id: int) -> Optional[tuple[str, str]]:
    """Returns (start_hhmm, end_hhmm) in IST, or None if not set."""
    raw = get_setting(user_id, "dnd_window")
    if not raw or "-" not in raw:
        return None
    start, end = raw.split("-", 1)
    return start, end


def set_dnd_window(user_id: int, start_hhmm: Optional[str], end_hhmm: Optional[str]) -> None:
    if start_hhmm is None or end_hhmm is None:
        set_setting(user_id, "dnd_window", "")
    else:
        set_setting(user_id, "dnd_window", f"{start_hhmm}-{end_hhmm}")


def is_within_dnd(user_id: int) -> bool:
    window = get_dnd_window(user_id)
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


def update_streak(user_id: int) -> None:
    today = _now_ist().date().isoformat()
    yesterday = (_now_ist() - timedelta(days=1)).date().isoformat()
    with get_connection() as conn:
        last = conn.execute(
            text("SELECT value FROM user_settings WHERE user_id=:u AND key='streak_last_date'"),
            {"u": user_id},
        ).scalar()
        if last == today:
            return
        current = int(conn.execute(
            text("SELECT value FROM user_settings WHERE user_id=:u AND key='streak_current'"),
            {"u": user_id},
        ).scalar() or "0")
        longest = int(conn.execute(
            text("SELECT value FROM user_settings WHERE user_id=:u AND key='streak_longest'"),
            {"u": user_id},
        ).scalar() or "0")
        current = current + 1 if last == yesterday else 1
        longest = max(longest, current)
        for key, value in (
            ("streak_last_date", today), ("streak_current", str(current)), ("streak_longest", str(longest)),
        ):
            conn.execute(
                text(
                    "INSERT INTO user_settings (user_id, key, value) VALUES (:u, :k, :v)"
                    " ON CONFLICT (user_id, key) DO UPDATE SET value=:v"
                ),
                {"u": user_id, "k": key, "v": value},
            )


def get_streak_info(user_id: int) -> dict:
    return {
        "current": int(get_setting(user_id, "streak_current") or "0"),
        "longest": int(get_setting(user_id, "streak_longest") or "0"),
        "last_date": get_setting(user_id, "streak_last_date"),
    }
