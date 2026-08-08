import asyncio
import logging
import os
from typing import Optional

from mcp.server.mcpserver import MCPServer

from studybot import db

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db.init_db()

mcp_server = MCPServer("study-bot")

_NO_CHAT = {"error": "No registered chat. Send /start to the bot first."}


@mcp_server.tool()
def add_card(
    question: str, answer: str, tags: str = "", notes: str = "", reverse: bool = False
) -> dict:
    """Add a single flashcard.

    Args:
        question: The prompt side. Keep under 300 chars — Telegram caps messages at 4096.
        answer: The recall side. Keep under 500 chars; split anything longer into two cards.
        tags: Optional comma-separated, e.g. 'python,algorithms'.
        notes: Optional extra context, shown only after the answer is revealed. Good for
            the "why" behind a fact without bloating the answer itself.
        reverse: If True, also create the mirror card (answer -> question), scheduled
            independently. Useful for term/definition pairs, wrong for one-way facts.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    tags_val = tags.strip() or None
    notes_val = notes.strip() or None
    if reverse:
        forward_id, reverse_id = db.add_card_with_reverse(
            question, answer, chat_id, tags=tags_val, notes=notes_val
        )
        return {"ids": [forward_id, reverse_id], "count": 2, "reverse": True}
    card_id = db.add_card(question, answer, chat_id, tags=tags_val, notes=notes_val)
    return {"id": card_id, "question": question, "answer": answer, "tags": tags_val}


@mcp_server.tool()
def add_cards_bulk(cards: list[dict]) -> dict:
    """Add multiple flashcards in one call.

    Args:
        cards: List of dicts with "question", "answer", and optional "tags"/"notes" keys.
            Keep question under 300 chars and answer under 500 chars per card.
            Prefer atomic cards — one fact each. For an "X vs Y vs Z" comparison,
            emit one card per item rather than cramming all three into one answer.

    Returns:
        Dict with ids (list of ints) and count of cards added.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    ids = db.add_cards_bulk(cards, chat_id)
    return {"ids": ids, "count": len(ids)}


@mcp_server.tool()
def edit_card(
    card_id: int, question: Optional[str] = None, answer: Optional[str] = None,
    tags: Optional[str] = None, notes: Optional[str] = None,
) -> dict:
    """Update an existing card in place. Only the fields you pass are changed.

    Scheduling state is untouched, so fixing a typo or tightening the wording of a
    card the user keeps failing does not reset its review history.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        return {"error": f"Card #{card_id} not found."}
    db.edit_card(card_id, question=question, answer=answer, tags=tags, notes=notes)
    return {"id": card_id, "updated": True, "card": db.get_card(card_id)}


@mcp_server.tool()
def delete_card(card_id: int) -> dict:
    """Permanently delete a card and its review history. Prefer suspend_card if the
    user might want it back — deletion cannot be undone."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        return {"error": f"Card #{card_id} not found."}
    db.delete_card(card_id)
    return {"id": card_id, "deleted": True}


@mcp_server.tool()
def suspend_card(card_id: int, suspended: bool = True) -> dict:
    """Take a card out of review rotation (or put it back with suspended=False).

    Reversible and history-preserving — the right move for a leech the user wants
    to park rather than lose.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        return {"error": f"Card #{card_id} not found."}
    db.set_suspended(card_id, suspended)
    return {"id": card_id, "suspended": suspended}


@mcp_server.tool()
def search_cards(keyword: str) -> list[dict]:
    """Find cards whose question or answer contains `keyword`.

    Use before adding cards to avoid creating a near-duplicate of something the
    user already has.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return []
    return db.search_cards(chat_id, keyword)


@mcp_server.tool()
def list_due_cards() -> list[dict]:
    """Return all cards currently due for review, excluding suspended and buried ones."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return []
    return db.list_due_cards(chat_id)


@mcp_server.tool()
def get_card_history(card_id: int) -> dict:
    """Full detail for one card plus its recent review log.

    Reveals FSRS memory state: stability (days until recall drops to the target
    retention) and difficulty (1-10). Low stability after many reviews means the
    card itself is probably badly written.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        return {"error": f"Card #{card_id} not found."}
    return {"card": card, "history": db.get_card_history(card_id, limit=20)}


@mcp_server.tool()
def get_stats() -> dict:
    """Deck totals: total cards, how many are due, suspended count, stage breakdown."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    return db.get_stats(chat_id)


@mcp_server.tool()
def get_retention_stats(days: int = 30) -> dict:
    """True retention over the last `days`: how often the user recalled a card they
    had already learned (each card's first review is excluded).

    Includes a per-tag breakdown sorted weakest-first — the fastest way to see which
    topic actually needs re-teaching rather than more drilling.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    return db.get_retention_stats(chat_id, days=days)


@mcp_server.tool()
def get_forecast(days: int = 7) -> dict:
    """Upcoming review load per day, so a study session can be planned around it.

    Returns an 'Overdue' bucket followed by one entry per upcoming day.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    forecast = db.get_forecast(chat_id, days=days)
    return {
        "forecast": [{"day": label, "due": count} for label, count in forecast],
        "daily_cap": db.get_daily_cap(chat_id),
    }


@mcp_server.tool()
def get_weak_cards() -> list[dict]:
    """Cards the user is struggling with — leeches (4+ wrong in a row) or ones FSRS
    rates as intrinsically hard.

    Use this to drive a re-teaching session: pull the weak cards, explain the
    underlying concept again, then either rewrite the card with edit_card (if the
    card is the problem) or let the user re-attempt it in the bot.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return []
    return db.list_weak_cards(chat_id)


def run() -> None:
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8811"))
    logger.info("Starting MCP server on http://%s:%d/mcp", host, port)
    asyncio.run(mcp_server.run_streamable_http_async(host=host, port=port, streamable_http_path="/mcp"))


if __name__ == "__main__":
    run()
