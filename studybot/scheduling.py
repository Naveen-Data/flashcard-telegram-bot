"""Policy layer that sits on top of FSRS: study windows, exam deadlines, caps.

FSRS decides *how many days* until a card should next be seen. These helpers
decide *when on that day* it lands, and clamp the interval when a deadline or
a user preference says the raw answer isn't what we want.
"""
from datetime import datetime, time, timedelta
from typing import Optional

IST = timedelta(hours=5, minutes=30)


def now_ist() -> datetime:
    return datetime.utcnow() + IST


def parse_hhmm(raw: str) -> Optional[time]:
    try:
        h, m = map(int, raw.strip().split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return time(hour=h, minute=m)
    except (ValueError, AttributeError):
        pass
    return None


def parse_window(raw: Optional[str]) -> Optional[tuple[time, time]]:
    """'21:00-23:00' -> (time(21,0), time(23,0)). None if unset or malformed."""
    if not raw or "-" not in raw:
        return None
    start_raw, end_raw = raw.split("-", 1)
    start, end = parse_hhmm(start_raw), parse_hhmm(end_raw)
    if start is None or end is None:
        return None
    return start, end


def apply_study_window(due_utc: datetime, window_raw: Optional[str], card_id: int) -> datetime:
    """Move a due timestamp into the user's study window on the same IST day.

    Without this a card comes due at whatever o'clock it happened to be created,
    so a late-night study session produces cards that surface at 1am forever.
    Cards are fanned out across the window (seeded by id) so a big batch doesn't
    all land on the same minute.
    """
    window = parse_window(window_raw)
    if window is None:
        return due_utc
    start, end = window
    due_ist = due_utc + IST

    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:  # window wraps past midnight
        end_minutes += 24 * 60
    span = max(1, end_minutes - start_minutes)
    offset = (card_id * 7919) % span  # deterministic spread, no RNG state needed

    target = due_ist.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        minutes=start_minutes + offset
    )
    return target - IST


def clamp_interval_for_exam(
    interval: int, exam_date_raw: Optional[str], now_utc: Optional[datetime] = None
) -> int:
    """Shrink an interval so a card is still revisited before its exam.

    Halving the remaining runway means you always get at least one more look
    before the deadline, with reviews naturally bunching up as it approaches.
    """
    if not exam_date_raw:
        return interval
    try:
        exam_date = datetime.strptime(exam_date_raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return interval
    today_ist = ((now_utc or datetime.utcnow()) + IST).date()
    days_left = (exam_date - today_ist).days
    if days_left <= 0:
        return interval  # exam has passed; back to normal scheduling
    return max(1, min(interval, days_left // 2 or 1))


def next_ist_midnight_utc(now_utc: Optional[datetime] = None) -> datetime:
    """UTC instant of the next IST midnight — used to bury a card until tomorrow."""
    base = (now_utc or datetime.utcnow()) + IST
    tomorrow = (base + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow - IST
