import asyncio
import hmac
import logging
import os
import time
from collections import defaultdict, deque
from typing import Optional

from mcp.server.mcpserver import MCPServer
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

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


@mcp_server.tool()
def add_session_note(topic: str, content: str, tags: str = "") -> dict:
    """Save a note from a study session (a summary, what was covered, traps to watch for).

    Separate from a card's own `notes` field — this is session-level, not tied to
    one card. Use at the end of a study-session to persist the "what we covered"
    summary so it shows up in the web app later.

    Args:
        topic: Short label for the session, e.g. 'RAG advanced retrieval'.
        content: The note body — summary, key ideas, traps. Markdown/plain text fine.
        tags: Optional comma-separated, e.g. 'rag,advanced'.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return _NO_CHAT
    note_id = db.add_session_note(chat_id, topic, content, tags=tags.strip() or None)
    return {"id": note_id, "topic": topic}


@mcp_server.tool()
def list_session_notes(limit: int = 20) -> list[dict]:
    """Return the most recent study-session notes, newest first."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return []
    return db.list_session_notes(chat_id, limit=limit)


from studybot.web import register as _register_web  # noqa: E402

_register_web(mcp_server)

# The web frontend (studybot-web) is deployed separately on Vercel, so browser
# requests to /api/* and /app cross origins. Vite's dev-server ports are allowed
# unconditionally so local frontend dev needs no env var; the deployed Vercel
# origin(s) come from WEB_ALLOWED_ORIGINS (comma-separated) once that's known.
_DEV_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5183", "http://127.0.0.1:5183",
]

# /app (the SPA shell) and /api/auth/* (register, login, status) stay reachable
# without any credential — the shell has to load and let someone log in before
# anything else is possible. CORS preflight OPTIONS is never gated either way.
_PUBLIC_PREFIXES = ("/api/auth/",)


class _RequireAuth:
    """Two different auth models behind one gate, because the two clients are
    different shapes: Claude Code (/mcp) sends one static secret it was configured
    with once; a browser (/api/*) logs a human in and gets a session token back.

    /mcp: no-op if MCP_AUTH_TOKEN is unset, so deploying this before the secret
    exists doesn't take the server down.
    /api/*: always enforced via studybot.db.verify_session — registration always
    works regardless of any env var.
    """

    def __init__(self, app, mcp_token: str | None):
        self.app = app
        self.mcp_token = mcp_token

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or path.startswith(_PUBLIC_PREFIXES)
            or not (path.startswith("/api/") or path.startswith("/mcp"))
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        token = auth[7:] if auth.startswith("Bearer ") else ""

        if path.startswith("/mcp"):
            ok = not self.mcp_token or hmac.compare_digest(token, self.mcp_token)
        else:
            ok = db.verify_session(token)

        if not ok:
            await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


# (path_prefix, method_or_None, max_requests, window_seconds) — first match wins,
# an unmatched path (e.g. /app) is never limited. Login/register get the tight
# limits since they're the actual brute-force/spam targets; /mcp and general
# /api/* just need a ceiling against a runaway client or script.
_RATE_LIMIT_RULES: list[tuple[str, Optional[str], int, float]] = [
    ("/api/auth/login", "POST", 5, 300),
    ("/api/auth/register", "POST", 3, 900),
    ("/mcp", None, 120, 60),
    ("/api/", None, 60, 60),
]


class _RateLimit:
    """Fixed-window rate limit per client IP, in memory.

    Fine for a single uvicorn process (this deployment); would need a shared
    store (e.g. Redis) if this ever ran behind multiple workers. Reads the
    real client from X-Forwarded-For when present — nginx must set that header
    for limits to be per-visitor rather than one shared bucket for everyone
    behind the proxy (`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`).
    """

    def __init__(self, app, rules=_RATE_LIMIT_RULES):
        self.app = app
        self.rules = rules
        self.hits: dict[tuple[str, int], deque] = defaultdict(deque)

    @staticmethod
    def _client_ip(scope) -> str:
        headers = dict(scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for")
        if forwarded:
            return forwarded.decode().split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path, method = scope["path"], scope["method"]
        for i, (prefix, rule_method, limit, window) in enumerate(self.rules):
            if not path.startswith(prefix) or (rule_method and rule_method != method):
                continue
            key = (self._client_ip(scope), i)
            hits = self.hits[key]
            now = time.monotonic()
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, round(window - (now - hits[0])))
                await JSONResponse(
                    {"error": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )(scope, receive, send)
                return
            hits.append(now)
            break

        await self.app(scope, receive, send)


def build_app(host: str = "127.0.0.1"):
    app = mcp_server.streamable_http_app(streamable_http_path="/mcp", host=host)
    app = _RequireAuth(app, os.environ.get("MCP_AUTH_TOKEN"))
    app = _RateLimit(app)
    extra_origins = [o.strip() for o in os.environ.get("WEB_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return CORSMiddleware(
        app,
        allow_origins=_DEV_ORIGINS + extra_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )


def run() -> None:
    import uvicorn

    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8811"))
    logger.info("Starting MCP server on http://%s:%d/mcp", host, port)
    uvicorn.run(build_app(host=host), host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
