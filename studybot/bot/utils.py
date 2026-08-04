import re
from datetime import datetime, timedelta

IST = timedelta(hours=5, minutes=30)
TG_MAX = 4096
TAG_RE = re.compile(r"#(\w+)")
CLOZE_RE = re.compile(r"\{\{c\d+::(.*?)\}\}")


def clip(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def ist_to_utc(hour: int, minute: int) -> tuple[int, int]:
    total = (hour * 60 + minute - 330) % (24 * 60)
    return total // 60, total % 60


def progress_bar(current: int, total: int, width: int = 10) -> str:
    if total == 0:
        return ""
    filled = round(current / total * width)
    return "█" * filled + "░" * (width - filled)


def snooze_delta(snooze_type: str) -> timedelta:
    now_ist = datetime.utcnow() + IST
    if snooze_type == "1h":
        return timedelta(hours=1)
    if snooze_type == "tonight":
        target = now_ist.replace(hour=20, minute=0, second=0, microsecond=0)
        if now_ist.hour >= 19:
            target += timedelta(days=1)
        delta = target - now_ist
        return delta if delta.total_seconds() > 0 else timedelta(hours=1)
    if snooze_type == "tomorrow":
        target = (now_ist + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return target - now_ist
    return timedelta(hours=1)


def is_cloze_text(text: str) -> bool:
    return bool(CLOZE_RE.search(text))


def cloze_front(text: str) -> str:
    return CLOZE_RE.sub("[...]", text)


def cloze_back(text: str) -> str:
    return CLOZE_RE.sub(r"\1", text)
