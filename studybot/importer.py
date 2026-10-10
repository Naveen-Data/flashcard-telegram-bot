"""Idempotent bulk import of a "study pack": flashcards plus session notes.

One JSON document, produced by any AI (or by hand), pushed by studybot_push.py
over HTTP or by the import_study_pack MCP tool — both land here, so the rules
can't drift.

    {"version": 1, "topic": "system-design",            # default topic for cards
     "cards": [{"question", "answer", "tags", "topic", "notes"}],
     "notes": [{"topic", "content", "tags"}]}

Upsert keys: a card is identified by its normalized question text, a note by its
normalized topic (title), both per user. Re-pushing the same pack is a no-op;
pushing a corrected answer updates the card in place and keeps its FSRS state.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from studybot.db.cards import _canon_tags, _canon_topic
from studybot.db.connection import get_connection

MAX_CARDS = 500
MAX_NOTES = 50
LIMITS = {"question": 1000, "answer": 2000, "notes": 2000, "topic": 100, "tags": 500, "content": 20000}


class PackError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class _DryRun(Exception):
    pass


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def _opt_str(item: dict, key: str, where: str, errors: list[str]) -> str | None:
    """Optional string field. Tags may also arrive as a list — AIs do both."""
    value = item.get(key)
    if value is None:
        return None
    if key == "tags" and isinstance(value, list):
        value = ",".join(str(t).strip() for t in value if str(t).strip())
    if not isinstance(value, str):
        errors.append(f"{where}.{key}: must be a string")
        return None
    value = value.strip()
    if len(value) > LIMITS[key]:
        errors.append(f"{where}.{key}: longer than {LIMITS[key]} characters")
    return value or None


def _required_str(item: dict, key: str, where: str, errors: list[str]) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}.{key}: required, non-empty string")
        return ""
    value = value.strip()
    if len(value) > LIMITS[key]:
        errors.append(f"{where}.{key}: longer than {LIMITS[key]} characters")
    return value


def validate(pack: Any) -> tuple[list[dict], list[dict]]:
    """Return clean (cards, notes), or raise PackError listing every problem at once —
    an AI can fix them all in one retry instead of one error per round trip."""
    if not isinstance(pack, dict):
        raise PackError(["pack must be a JSON object"])
    errors: list[str] = []
    cards_in, notes_in = pack.get("cards") or [], pack.get("notes") or []
    if not isinstance(cards_in, list) or not isinstance(notes_in, list):
        raise PackError(["cards and notes must be arrays"])
    if not cards_in and not notes_in:
        raise PackError(["pack has no cards and no notes"])
    if len(cards_in) > MAX_CARDS or len(notes_in) > MAX_NOTES:
        raise PackError([f"at most {MAX_CARDS} cards and {MAX_NOTES} notes per pack"])

    default_topic = _opt_str(pack, "topic", "pack", errors)
    cards: list[dict] = []
    for i, item in enumerate(cards_in):
        where = f"cards[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        cards.append({
            "question": _required_str(item, "question", where, errors),
            "answer": _required_str(item, "answer", where, errors),
            "tags": _opt_str(item, "tags", where, errors),
            "topic": _opt_str(item, "topic", where, errors) or default_topic,
            "notes": _opt_str(item, "notes", where, errors),
        })
    notes: list[dict] = []
    for i, item in enumerate(notes_in):
        where = f"notes[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        notes.append({
            "topic": _required_str(item, "topic", where, errors),
            "content": _required_str(item, "content", where, errors),
            "tags": _opt_str(item, "tags", where, errors),
        })
    if errors:
        raise PackError(errors)
    return cards, notes


def _upsert_cards(conn, user_id: int, cards: list[dict]) -> dict:
    existing: dict[str, dict] = {}
    rows = conn.execute(
        text("SELECT id, question, answer, tags, topic, notes FROM cards WHERE user_id=:u ORDER BY id"),
        {"u": user_id},
    )
    for r in rows:  # ponytail: full per-user scan; index a normalized-question column past ~50k cards
        existing.setdefault(_norm(r.question), dict(r._mapping))

    now = datetime.now(timezone.utc)
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for card in cards:
        key = _norm(card["question"])
        tags, topic = _canon_tags(card["tags"]), _canon_topic(card["topic"])
        found = existing.get(key)
        if found is None:
            new_id = conn.execute(
                text(
                    "INSERT INTO cards (question, answer, stage, due_at, created_at, user_id, tags, topic,"
                    " ease_factor, interval_days, repetitions, notes)"
                    " VALUES (:q, :a, 0, :due, :now, :u, :tags, :topic, 2.5, 1, 0, :notes) RETURNING id"
                ),
                {"q": card["question"], "a": card["answer"], "due": now + timedelta(days=1), "now": now,
                 "u": user_id, "tags": tags, "topic": topic, "notes": card["notes"]},
            ).scalar_one()
            # A repeat of the same question later in this pack becomes an update, not a duplicate.
            existing[key] = {"id": new_id, "question": card["question"], "answer": card["answer"],
                             "tags": tags, "topic": topic, "notes": card["notes"]}
            counts["created"] += 1
            continue
        # Optional fields only overwrite when the pack supplies them, so a pack
        # without notes never wipes notes the user added by hand.
        changes = {"answer": card["answer"]}
        for column, value in (("tags", tags), ("topic", topic), ("notes", card["notes"])):
            if value is not None:
                changes[column] = value
        if all(found[c] == v for c, v in changes.items()):
            counts["unchanged"] += 1
            continue
        sets = ", ".join(f"{c}=:{c}" for c in changes)
        conn.execute(text(f"UPDATE cards SET {sets} WHERE id=:id AND user_id=:u"),
                     {**changes, "id": found["id"], "u": user_id})
        found.update(changes)
        counts["updated"] += 1
    return counts


def _upsert_notes(conn, user_id: int, notes: list[dict]) -> dict:
    existing: dict[str, dict] = {}
    rows = conn.execute(
        text("SELECT id, topic, content, tags FROM session_notes WHERE user_id=:u ORDER BY id"), {"u": user_id}
    )
    for r in rows:
        existing.setdefault(_norm(r.topic), dict(r._mapping))

    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for note in notes:
        found = existing.get(_norm(note["topic"]))
        if found is None:
            conn.execute(
                text(
                    "INSERT INTO session_notes (user_id, topic, content, tags, created_at)"
                    " VALUES (:u, :topic, :content, :tags, :now)"
                ),
                {"u": user_id, "topic": note["topic"], "content": note["content"], "tags": note["tags"],
                 "now": datetime.now(timezone.utc)},
            )
            counts["created"] += 1
            continue
        tags = note["tags"] if note["tags"] is not None else found["tags"]
        if found["content"] == note["content"] and found["tags"] == tags:
            counts["unchanged"] += 1
            continue
        conn.execute(
            text("UPDATE session_notes SET content=:content, tags=:tags WHERE id=:id AND user_id=:u"),
            {"content": note["content"], "tags": tags, "id": found["id"], "u": user_id},
        )
        found.update(content=note["content"], tags=tags)
        counts["updated"] += 1
    return counts


def import_pack(user_id: int, pack: Any, dry_run: bool = False) -> dict:
    """Validate then upsert everything in one transaction. Raises PackError
    before touching the database if anything is malformed. dry_run reports
    what would change and rolls back."""
    cards, notes = validate(pack)
    result: dict = {}
    try:
        with get_connection() as conn:
            result = {"cards": _upsert_cards(conn, user_id, cards), "notes": _upsert_notes(conn, user_id, notes)}
            if dry_run:
                raise _DryRun()
    except _DryRun:
        pass
    result["dry_run"] = dry_run
    return result
