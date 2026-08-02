# Task Context

## Language
Python — standarts from `.claude/skills/python-code-standarts.md`

## Key Standarts for This Task
- Always use type hints; prefer built-in generics (`list[dict]`, `Optional[int]`)
- `snake_case` for functions/variables, `UPPER_CASE` for module-level constants
- Group imports: stdlib → third-party → local; no wildcard imports
- Catch specific exceptions — never bare `except`
- Never hardcode secrets; read from environment variables
- Max line length 88 chars (Black standard)
- Use `Path` from pathlib for file paths; never raw string paths
- Docstrings on all public functions (Google style)

## Task
Build a local Telegram-based Anki-style spaced-repetition flashcard system from scratch: `db.py`, `bot.py`, `mcp_server.py`, `requirements.txt`, `README.md`, `.gitignore`, and an empty `data/` directory.

## Plan
- Step 1: Create `data/` directory placeholder (`.gitkeep`)
- Step 2: Write `db.py` — full SQLite layer with all specified functions plus `get_card` helper
- Step 3: Write `bot.py` — async Telegram bot with commands, background job, and callback handler
- Step 4: Write `mcp_server.py` — FastMCP streamable-http server on 127.0.0.1:8811
- Step 5: Write `requirements.txt`
- Step 6: Write `README.md` verbatim as provided
- Step 7: Write `.gitignore`

## Files to Change
- `db.py`: Create from scratch — SQLite layer
- `bot.py`: Create from scratch — async Telegram bot
- `mcp_server.py`: Create from scratch — MCP server
- `requirements.txt`: Create from scratch
- `README.md`: Create from scratch — verbatim content provided
- `.gitignore`: Create from scratch
- `data/.gitkeep`: Create empty file so `data/` is tracked by git

## Exact Signatures

```python
# db.py
INTERVALS_DAYS: list[int] = [1, 2, 5, 12, 26, 45]
DB_PATH: Path = Path(__file__).parent / "data" / "cards.db"

def init_db() -> None: ...
def add_card(question: str, answer: str, chat_id: int) -> int: ...
def add_cards_bulk(cards: list[dict], chat_id: int) -> list[int]: ...
def list_due_cards(chat_id: int) -> list[dict]: ...
def get_stats(chat_id: int) -> dict: ...
def get_registered_chat_id() -> Optional[int]: ...
def set_registered_chat_id(chat_id: int) -> None: ...
def record_answer(card_id: int, correct: bool) -> None: ...
def snooze_card(card_id: int, delta: timedelta) -> None: ...
def get_card(card_id: int) -> Optional[dict]: ...  # needed by bot.py reveal

# bot.py top-level
SENT_CARD_IDS: set[int] = set()  # module-level, in-memory, survives between job ticks

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def handle_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def send_due_cards(context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
def _build_due_keyboard(card_id: int) -> InlineKeyboardMarkup: ...
def _build_answer_keyboard(card_id: int) -> InlineKeyboardMarkup: ...
def _snooze_delta(snooze_type: str) -> timedelta: ...
def main() -> None: ...

# mcp_server.py
mcp_server = FastMCP("study-bot")
# tools decorated with @mcp_server.tool()
```

## Types Needed
- No new TypedDict needed; cards are returned as `dict` (from `sqlite3.Row` via `dict(row)`)
- `Optional` from `typing` for Python <3.10 compat; use `from typing import Optional`
- `timedelta` from `datetime` stdlib

## Patterns to Follow

```python
# db.py pattern — connection factory with row_factory, used for every query
def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Using context manager for auto-close; explicit commit before returning
with _get_connection() as conn:
    cursor = conn.execute("INSERT INTO ...", (...,))
    conn.commit()
    return cursor.lastrowid
```

```python
# bot.py pattern — inline keyboard construction
def _build_due_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Reveal", callback_data=f"reveal:{card_id}")],
        [
            InlineKeyboardButton("⏰ 1h", callback_data=f"snooze:1h:{card_id}"),
            InlineKeyboardButton("🌙 Tonight", callback_data=f"snooze:tonight:{card_id}"),
            InlineKeyboardButton("📅 Tomorrow", callback_data=f"snooze:tomorrow:{card_id}"),
        ],
    ])
```

```python
# mcp_server.py pattern — FastMCP tool registration
from mcp.server.fastmcp import FastMCP

mcp_server = FastMCP("study-bot")

@mcp_server.tool()
def add_card(question: str, answer: str) -> dict:
    """Add a single flashcard to the deck."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return {"error": "No registered chat. Send /start to the bot first."}
    card_id = db.add_card(question, answer, chat_id)
    return {"id": card_id, "question": question, "answer": answer}
```

## Anti-patterns — Do NOT do this

- DO NOT use `datetime.now()` for DB timestamps — use `datetime.utcnow()` consistently so all stored times are UTC; local-time conversion is only for computing snooze deltas
- DO NOT use `Path("data/cards.db")` (relative) — use `Path(__file__).parent / "data" / "cards.db"` so the script works from any working directory
- DO NOT split the "add: question / answer" text on the first "/" — use `body.partition(" / ")` (with spaces) so questions containing "/" (e.g. "What is 5/2?") are parsed correctly
- DO NOT open a new db connection per card in `add_cards_bulk` — open one connection, loop inserts, single commit
- DO NOT bare-except in callback handler — use `except Exception as e: logger.error(...)` at minimum
- DO NOT forget to `discard` card_id from `SENT_CARD_IDS` after `record_answer` or `snooze_card` — otherwise cards won't be re-sent if something goes wrong

## Public API Changes
No — this is a new standalone project with no existing package.

## Edge Cases to Handle

- `TELEGRAM_BOT_TOKEN` missing: `sys.exit("Error: TELEGRAM_BOT_TOKEN environment variable not set.")` before any async code
- No registered chat_id when job fires: `if chat_id is None: return` immediately
- `reveal:{card_id}` callback when card was deleted: `if card is None: await query.edit_message_text("Card not found."); return`
- "tonight" snooze when it's already past 7pm: target becomes 8pm **tomorrow**, not 8pm today — check `now_local.hour >= 19`
- `record_answer` called on non-existent card_id: fetch row first, return early if None
- `add_cards_bulk` called with empty list: return `[]` immediately (loop handles it, but worth being explicit)
- Stage at maximum (5): `min(current_stage + 1, len(INTERVALS_DAYS) - 1)` clamps correctly

## Self-critique Notes

Draft review found three gaps and fixed them:
1. The spec's `db.py` function list has no `get_card()` but `bot.py`'s reveal callback needs the answer text. Added `get_card(card_id) -> Optional[dict]` to db.py.
2. Naive `body.partition("/")` fails for questions like "What is 5/2?". Fixed to `body.partition(" / ")` (space-slash-space).
3. `DB_PATH = Path("data/cards.db")` breaks if the script is run from a different directory. Fixed to `Path(__file__).parent / "data" / "cards.db"`.

---

## File Contents

### db.py

```python
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "cards.db"
INTERVALS_DAYS: list[int] = [1, 2, 5, 12, 26, 45]


def _get_connection() -> sqlite3.Connection:
    """Open a SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                stage INTEGER NOT NULL DEFAULT 0,
                due_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                chat_id INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()


def add_card(question: str, answer: str, chat_id: int) -> int:
    """Add a single card and return its id.

    Args:
        question: The front of the card.
        answer: The back of the card.
        chat_id: Telegram chat id to associate with this card.

    Returns:
        The new card's integer id.
    """
    now = datetime.utcnow()
    due_at = (now + timedelta(days=INTERVALS_DAYS[0])).isoformat()
    with _get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id)"
            " VALUES (?, ?, 0, ?, ?, ?)",
            (question, answer, due_at, now.isoformat(), chat_id),
        )
        conn.commit()
        return cursor.lastrowid


def add_cards_bulk(cards: list[dict], chat_id: int) -> list[int]:
    """Add multiple cards in a single transaction.

    Args:
        cards: List of dicts each containing "question" and "answer" keys.
        chat_id: Telegram chat id to associate with all cards.

    Returns:
        List of new card ids in insertion order.
    """
    if not cards:
        return []
    now = datetime.utcnow()
    due_at = (now + timedelta(days=INTERVALS_DAYS[0])).isoformat()
    now_iso = now.isoformat()
    ids: list[int] = []
    with _get_connection() as conn:
        for card in cards:
            cursor = conn.execute(
                "INSERT INTO cards (question, answer, stage, due_at, created_at, chat_id)"
                " VALUES (?, ?, 0, ?, ?, ?)",
                (card["question"], card["answer"], due_at, now_iso, chat_id),
            )
            ids.append(cursor.lastrowid)
        conn.commit()
    return ids


def list_due_cards(chat_id: int) -> list[dict]:
    """Return all cards that are currently due for the given chat.

    Args:
        chat_id: Telegram chat id to filter by.

    Returns:
        List of card dicts with all columns.
    """
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE chat_id = ? AND due_at <= ?",
            (chat_id, now),
        ).fetchall()
        return [dict(row) for row in rows]


def get_stats(chat_id: int) -> dict:
    """Return aggregate stats for the given chat.

    Args:
        chat_id: Telegram chat id to filter by.

    Returns:
        Dict with keys: total (int), due (int), by_stage (dict[int, int]).
    """
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        total: int = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()[0]
        due: int = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE chat_id = ? AND due_at <= ?",
            (chat_id, now),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT stage, COUNT(*) AS count FROM cards WHERE chat_id = ? GROUP BY stage",
            (chat_id,),
        ).fetchall()
        by_stage: dict[int, int] = {row["stage"]: row["count"] for row in rows}
    return {"total": total, "due": due, "by_stage": by_stage}


def get_registered_chat_id() -> Optional[int]:
    """Return the stored Telegram chat id, or None if not yet registered."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'chat_id'"
        ).fetchone()
        if row:
            return int(row["value"])
        return None


def set_registered_chat_id(chat_id: int) -> None:
    """Store or replace the registered Telegram chat id.

    Args:
        chat_id: Telegram chat id to register.
    """
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)",
            (str(chat_id),),
        )
        conn.commit()


def record_answer(card_id: int, correct: bool) -> None:
    """Advance or reset a card's stage and reschedule it.

    A correct answer advances the stage by 1 (capped at the last interval).
    A wrong answer resets stage to 0 (due tomorrow).

    Args:
        card_id: The card to update.
        correct: True if the user answered correctly, False otherwise.
    """
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT stage FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            return
        current_stage: int = row["stage"]
        if correct:
            new_stage = min(current_stage + 1, len(INTERVALS_DAYS) - 1)
        else:
            new_stage = 0
        interval = INTERVALS_DAYS[new_stage]
        due_at = (datetime.utcnow() + timedelta(days=interval)).isoformat()
        conn.execute(
            "UPDATE cards SET stage = ?, due_at = ? WHERE id = ?",
            (new_stage, due_at, card_id),
        )
        conn.commit()


def snooze_card(card_id: int, delta: timedelta) -> None:
    """Push a card's due_at forward by delta without changing its stage.

    Args:
        card_id: The card to snooze.
        delta: How far into the future to reschedule from now.
    """
    new_due = (datetime.utcnow() + delta).isoformat()
    with _get_connection() as conn:
        conn.execute(
            "UPDATE cards SET due_at = ? WHERE id = ?",
            (new_due, card_id),
        )
        conn.commit()


def get_card(card_id: int) -> Optional[dict]:
    """Fetch a single card by id.

    Args:
        card_id: Primary key of the card.

    Returns:
        Card dict or None if not found.
    """
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return dict(row) if row else None
```

### bot.py

```python
import logging
import os
import sys
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# In-memory set of card ids already pushed this session to avoid duplicates.
SENT_CARD_IDS: set[int] = set()


# ---------------------------------------------------------------------------
# Keyboard helpers
# ---------------------------------------------------------------------------


def _build_due_keyboard(card_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown with the initial due-card message."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Reveal", callback_data=f"reveal:{card_id}")],
        [
            InlineKeyboardButton("⏰ 1h", callback_data=f"snooze:1h:{card_id}"),
            InlineKeyboardButton("🌙 Tonight", callback_data=f"snooze:tonight:{card_id}"),
            InlineKeyboardButton("📅 Tomorrow", callback_data=f"snooze:tomorrow:{card_id}"),
        ],
    ])


def _build_answer_keyboard(card_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown after revealing the answer."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Got it right", callback_data=f"correct:{card_id}"),
            InlineKeyboardButton("❌ Got it wrong", callback_data=f"wrong:{card_id}"),
        ]
    ])


def _snooze_delta(snooze_type: str) -> timedelta:
    """Compute a timedelta from now based on the snooze type.

    Args:
        snooze_type: One of "1h", "tonight", "tomorrow".

    Returns:
        timedelta from the current moment to the desired snooze target.
    """
    now_local = datetime.now()

    if snooze_type == "1h":
        return timedelta(hours=1)

    if snooze_type == "tonight":
        target = now_local.replace(hour=20, minute=0, second=0, microsecond=0)
        if now_local.hour >= 19:  # past 7pm — push to 8pm tomorrow
            target += timedelta(days=1)
        delta = target - now_local
        # Guard: if the calculation somehow yields a non-positive delta, fall back.
        return delta if delta.total_seconds() > 0 else timedelta(hours=1)

    if snooze_type == "tomorrow":
        target = (now_local + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return target - now_local

    # Unknown type — default to 1 hour
    logger.warning("Unknown snooze type: %s, defaulting to 1h", snooze_type)
    return timedelta(hours=1)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register the current chat and confirm to the user."""
    chat_id = update.effective_chat.id
    db.set_registered_chat_id(chat_id)
    await update.message.reply_text(
        "✅ Registered! I'll send you cards as they come due.\n\n"
        "To add a card:\n`add: question / answer`",
        parse_mode="Markdown",
    )


async def handle_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse 'add: question / answer' messages and add the card to the deck."""
    text = update.message.text.strip()
    if not text.lower().startswith("add:"):
        return

    body = text[4:].strip()

    # Use " / " as delimiter so questions containing "/" are handled correctly.
    if " / " not in body:
        await update.message.reply_text(
            "❌ Format: `add: question / answer`",
            parse_mode="Markdown",
        )
        return

    question, _, answer = body.partition(" / ")
    question = question.strip()
    answer = answer.strip()

    if not question or not answer:
        await update.message.reply_text(
            "❌ Both question and answer must be non-empty."
        )
        return

    chat_id = update.effective_chat.id
    card_id = db.add_card(question, answer, chat_id)
    await update.message.reply_text(f"✅ Card #{card_id} added.")


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------


async def send_due_cards(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: send each due card once per session."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return

    due_cards = db.list_due_cards(chat_id)
    for card in due_cards:
        card_id: int = card["id"]
        if card_id in SENT_CARD_IDS:
            continue
        SENT_CARD_IDS.add(card_id)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📚 Due: {card['question']}",
                reply_markup=_build_due_keyboard(card_id),
            )
        except Exception as e:
            logger.error("Failed to send card %d: %s", card_id, e)
            SENT_CARD_IDS.discard(card_id)  # retry next tick


# ---------------------------------------------------------------------------
# Callback query handler
# ---------------------------------------------------------------------------


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline button callbacks."""
    query = update.callback_query
    await query.answer()
    data: str = query.data

    try:
        if data.startswith("reveal:"):
            card_id = int(data.split(":")[1])
            card = db.get_card(card_id)
            if card is None:
                await query.edit_message_text("Card not found.")
                return
            await query.edit_message_text(
                f"📚 {card['question']}\n\n💡 {card['answer']}",
                reply_markup=_build_answer_keyboard(card_id),
            )

        elif data.startswith("correct:"):
            card_id = int(data.split(":")[1])
            db.record_answer(card_id, correct=True)
            SENT_CARD_IDS.discard(card_id)
            card = db.get_card(card_id)
            next_date = card["due_at"][:10] if card else "N/A"
            await query.edit_message_text(f"✅ Scheduled for {next_date}")

        elif data.startswith("wrong:"):
            card_id = int(data.split(":")[1])
            db.record_answer(card_id, correct=False)
            SENT_CARD_IDS.discard(card_id)
            await query.edit_message_text("❌ Resetting — due tomorrow")

        elif data.startswith("snooze:"):
            parts = data.split(":")
            snooze_type = parts[1]
            card_id = int(parts[2])
            delta = _snooze_delta(snooze_type)
            db.snooze_card(card_id, delta)
            SENT_CARD_IDS.discard(card_id)
            label_map = {"1h": "1 hour", "tonight": "tonight", "tomorrow": "tomorrow"}
            label = label_map.get(snooze_type, snooze_type)
            await query.edit_message_text(f"⏰ Snoozed until {label}")

        else:
            logger.warning("Unknown callback data: %s", data)

    except Exception as e:
        logger.error("Error handling callback %s: %s", data, e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Initialise db, build the application, register handlers, and run."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit(
            "Error: TELEGRAM_BOT_TOKEN environment variable not set.\n"
            "Export it before running: export TELEGRAM_BOT_TOKEN='your-token-here'"
        )

    db.init_db()

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_card)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Start the background job: check for due cards every 60 seconds.
    application.job_queue.run_repeating(send_due_cards, interval=60, first=10)

    logger.info("Bot started. Polling for updates...")
    application.run_polling()


if __name__ == "__main__":
    main()
```

### mcp_server.py

```python
import logging

from mcp.server.fastmcp import FastMCP

import db

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db.init_db()

mcp_server = FastMCP("study-bot")


@mcp_server.tool()
def add_card(question: str, answer: str) -> dict:
    """Add a single flashcard to the deck.

    Args:
        question: The front of the card (what to recall).
        answer: The back of the card (the correct answer).

    Returns:
        Dict with id, question, and answer of the created card,
        or an error key if no chat is registered.
    """
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return {"error": "No registered chat. Send /start to the bot first."}
    card_id = db.add_card(question, answer, chat_id)
    return {"id": card_id, "question": question, "answer": answer}


@mcp_server.tool()
def add_cards_bulk(cards: list[dict]) -> dict:
    """Add multiple flashcards to the deck in a single call.

    Args:
        cards: List of dicts, each with "question" and "answer" string keys.

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
    logger.info("Starting MCP server on http://127.0.0.1:8811/mcp")
    mcp_server.run(transport="streamable-http", host="127.0.0.1", port=8811, path="/mcp")
```

### requirements.txt

```
python-telegram-bot>=20.0
mcp>=1.0
```

### README.md

```
# Study Bot — a local, Telegram-based Anki alternative

A minimal spaced-repetition flashcard system that lives entirely on your Mac. Telegram is just the UI (free, already on all your devices, push notifications built in) — all card data and scheduling logic stays in a local SQLite file.

## What it does

* Add cards manually in Telegram: `add: question / answer`
* Or have Claude add cards automatically via MCP after a study session
* Cards get pushed to you as soon as they're due, with tappable buttons: Reveal → Got it right / Got it wrong → auto-reschedules
* "Remind me in 1h / tonight / tomorrow" snooze buttons if you're busy
* Expanding spaced-repetition intervals: 1 → 2 → 5 → 12 → 26 → 45 days, correct answers advance the stage, wrong answers reset it

## Setup

### 1. Create your bot
Message @BotFather on Telegram → `/newbot` → follow the prompts → copy the token it gives you.

### 2. Install dependencies

```bash
cd study-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the bot

```bash
export TELEGRAM_BOT_TOKEN="paste-your-token-here"
python bot.py
```

Then open Telegram, find your bot, and send `/start`. That registers your chat so it knows where to push cards.

### 4. (Optional) Run the MCP server

In a second terminal:

```bash
source venv/bin/activate
python mcp_server.py
```

This starts an MCP server on `http://127.0.0.1:8811/mcp` exposing `add_card`, `add_cards_bulk`, `list_due_cards`, and `get_stats`. Point your MCP-aware Claude client at that URL and I can write cards directly into your deck after a study session.

### 5. Keep it running

For it to actually ping you in the background (not just while a terminal is open), you'll want to either:

* Leave a Terminal window open while you're using your Mac, or
* Set it up as a `launchd` background service (Claude Code can help you write the `.plist` file for this if you want it fully automatic)

## Notes

* All data lives in `data/cards.db` — back it up like any file, or don't; entirely up to you.
* Telegram messages do route through Telegram's servers to sync across your devices (that's how Telegram works everywhere) — the trade-off you chose for simplicity over strict local-only.
* The interval schedule lives in `db.py` (`INTERVALS_DAYS`) if you want to tune it.
```

### .gitignore

```
venv/
data/*.db
__pycache__/
.env
*.pyc
```

### data/.gitkeep

```
```
