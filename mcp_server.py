import asyncio
import logging
import os

from mcp.server.mcpserver import MCPServer

import db

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db.init_db()

mcp_server = MCPServer("study-bot")


@mcp_server.tool()
def add_card(question: str, answer: str, tags: str = "") -> dict:
    """Add a single flashcard. tags is optional comma-separated e.g. 'python,algorithms'.

    Telegram limit: question + answer combined should stay under 4096 chars per message.
    Keep each field concise — question under 300 chars, answer under 500 chars is a safe rule.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return {"error": "No registered chat. Send /start to the bot first."}
    tags_val = tags.strip() or None
    card_id = db.add_card(question, answer, chat_id, tags=tags_val)
    return {"id": card_id, "question": question, "answer": answer, "tags": tags_val}


@mcp_server.tool()
def add_cards_bulk(cards: list[dict]) -> dict:
    """Add multiple flashcards to the deck in a single call.

    Args:
        cards: List of dicts, each with "question", "answer", and optional "tags" string keys.
            Telegram limit: keep question under 300 chars and answer under 500 chars per card.

    Returns:
        Dict with ids (list of ints) and count of cards added,
        or an error key if no chat is registered.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return {"error": "No registered chat. Send /start to the bot first."}
    ids = db.add_cards_bulk(cards, chat_id)
    return {"ids": ids, "count": len(ids)}


@mcp_server.tool()
def list_due_cards() -> list[dict]:
    """Return all cards that are currently due for review.

    Returns:
        List of card dicts (id, question, answer, stage, due_at, created_at, chat_id).
        Empty list if no chat is registered or no cards are due.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return []
    return db.list_due_cards(chat_id)


@mcp_server.tool()
def get_stats() -> dict:
    """Return aggregate statistics for the registered chat's deck.

    Returns:
        Dict with total (int), due (int), by_stage (dict mapping stage to count).
        Returns an error key if no chat is registered.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return {"error": "No registered chat. Send /start to the bot first."}
    return db.get_stats(chat_id)


if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8811"))
    logger.info("Starting MCP server on http://%s:%d/mcp", host, port)
    asyncio.run(mcp_server.run_streamable_http_async(host=host, port=port, streamable_http_path="/mcp"))
