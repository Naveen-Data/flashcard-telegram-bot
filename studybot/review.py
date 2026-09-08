"""One review, applied. Shared by the Telegram bot and the web app so the
scheduling side effects (FSRS update, log, undo snapshot, streak) can't drift apart.
"""
from typing import Optional

from studybot import db
from studybot.bot import utils


def apply_review(user_id: int, card_id: int, quality: int) -> Optional[dict]:
    """Record an answer. Returns the updated card, or None if it doesn't exist or isn't owned."""
    pre = db.get_card(user_id, card_id)
    if pre is None:
        return None
    updated = db.record_answer(
        user_id, card_id, quality,
        desired_retention=db.get_desired_retention(user_id),
        study_window=db.get_study_window(user_id),
        exam_date=db.get_exam_date_for_card(user_id, pre.get("tags")),
    )
    log_id = db.log_review(user_id, card_id, quality)
    db.save_undo_snapshot(user_id, pre, log_id)
    db.update_streak(user_id)
    return updated


def card_payload(card: dict) -> dict:
    """Card as the web client wants it — cloze already rendered both ways."""
    if card.get("card_type") == "cloze":
        front, back = utils.cloze_front(card["question"]), utils.cloze_back(card["question"])
    else:
        front, back = card["question"], card["answer"]
    due_at = card.get("due_at")
    return {
        "id": card["id"],
        "front": front,
        "back": back,
        "question": card["question"],
        "answer": card["answer"],
        "card_type": card.get("card_type"),
        "tags": card.get("tags") or "",
        "topic": card.get("topic") or "",
        "notes": card.get("notes") or "",
        "due_at": due_at.isoformat() if due_at else None,
    }
