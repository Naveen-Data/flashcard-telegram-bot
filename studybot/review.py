"""One review, applied. Shared by the Telegram bot and the web app so the
scheduling side effects (FSRS update, log, undo snapshot, streak) can't drift apart.
"""
from typing import Optional

from studybot import db
from studybot.bot import utils


def apply_review(chat_id: int, card_id: int, quality: int) -> Optional[dict]:
    """Record an answer. Returns the updated card, or None if it doesn't exist."""
    pre = db.get_card(card_id)
    if pre is None:
        return None
    db.record_answer(
        card_id, quality,
        desired_retention=db.get_desired_retention(chat_id),
        study_window=db.get_study_window(chat_id),
        exam_date=db.get_exam_date_for_card(chat_id, pre.get("tags")),
    )
    log_id = db.log_review(chat_id, card_id, quality)
    db.save_undo_snapshot(chat_id, pre, log_id)
    db.update_streak(chat_id)
    return db.get_card(card_id)


def card_payload(card: dict) -> dict:
    """Card as the web client wants it — cloze already rendered both ways."""
    if card.get("card_type") == "cloze":
        front, back = utils.cloze_front(card["question"]), utils.cloze_back(card["question"])
    else:
        front, back = card["question"], card["answer"]
    return {
        "id": card["id"],
        "front": front,
        "back": back,
        "tags": card.get("tags") or "",
        "notes": card.get("notes") or "",
        "due_at": card.get("due_at"),
    }
