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
