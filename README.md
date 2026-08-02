 # Study Buddy — Telegram Spaced Repetition Bot

A personal study companion that lives in Telegram. Add flashcards, get notified when they're due, review with SM-2 spaced repetition (same algorithm as Anki), track streaks, and let Claude add cards directly after study sessions via MCP.

## Features

- **SM-2 spaced repetition** — 4-button review (Again / Hard / Good / Easy), intervals adapt per card
- **Tag-based organisation** — group cards by subject, review by tag
- **Study streaks & daily goals** — tracks consistency, celebrates milestones
- **Daily digest** — morning summary of due cards by tag (configurable time, IST)
- **Weekly report** — accuracy, active days, streak summary every Sunday
- **Session summary** — accuracy %, time taken, cards reviewed after each session
- **Pomodoro timer** — focus timer with Telegram ping when done
- **Search** — find cards by keyword across questions and answers
- **Snooze** — defer a card 1h / tonight / tomorrow without resetting its stage
- **MCP server** — Claude can add cards directly from any study session

## Commands

| Command | Description |
|---|---|
| `/start` | Register with the bot |
| `/help` | Show all commands |
| `/review` | Review due cards |
| `/review rag` | Review only #rag cards |
| `/review all` | Review all cards regardless of due date |
| `/review rag all` | All #rag cards regardless of due date |
| `/tags` | List all tags with card counts and due counts |
| `/search keyword` | Search cards by keyword |
| `/streak` | Current streak, longest, today's goal progress |
| `/goal 10` | Set daily card target |
| `/setdigest` | Show current daily digest time |
| `/setdigest 14:00` | Set daily digest time (IST, 24h) |
| `/pomodoro` | 25 min focus timer |
| `/pomodoro 45` | Custom timer in minutes |

## Adding Cards

```
add: question / answer
add: question / answer #tag1 #tag2
```

## SM-2 Algorithm

Each card tracks `ease_factor` (starts 2.5), `interval_days`, and `repetitions`.

| Button | Quality | Effect |
|---|---|---|
| 🔴 Again | 1 | Resets to 1 day, ease_factor drops |
| 🟠 Hard | 3 | interval × 1.2, ease_factor drops slightly |
| 🟢 Good | 4 | interval × ease_factor |
| 🔵 Easy | 5 | interval × ease_factor with bonus, ease_factor increases |

---

## Setup

### Prerequisites

- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create `.env`

```bash
echo "TELEGRAM_BOT_TOKEN=your-token-here" > .env
chmod 600 .env
```

### 3. Run locally

In two separate terminals:

```bash
# Terminal 1 — bot
source .env && python bot.py

# Terminal 2 — MCP server
source .env && python mcp_server.py
```

Send `/start` to your bot in Telegram to register your chat.

### 4. Run as background services (Mac)

Installs both as launchd agents — auto-start on login, no terminal needed:

```bash
bash deploy/local_setup.sh
```

```bash
# Check logs
tail -f logs/bot.log
tail -f logs/mcp.log

# Restart after code changes
launchctl unload ~/Library/LaunchAgents/com.studybot.bot.plist
launchctl load ~/Library/LaunchAgents/com.studybot.bot.plist
```

---


## MCP Integration (Claude)

The MCP server lets Claude add cards directly from any study session.

### Connect

```bash
# Local
claude mcp add study-bot --transport http http://127.0.0.1:8811/mcp --scope user

# Cloud VM
claude mcp add study-bot --transport http http://<VM_IP>:8811/mcp --scope user
```

### Available tools

| Tool | Description |
|---|---|
| `add_card(question, answer, tags)` | Add a single card |
| `add_cards_bulk(cards)` | Add multiple cards at once — each card: `{question, answer, tags}` |
| `list_due_cards()` | List currently due cards |
| `get_stats()` | Total cards, due count, breakdown by stage |

Tags are optional and comma-separated, e.g. `"rag,embeddings"`.

---

## Project Structure

```
├── bot.py                        # Telegram bot — all commands, jobs, SM-2 flow
├── db.py                         # SQLite layer, SM-2 logic, streak tracking
├── mcp_server.py                 # MCP server for Claude integration
├── requirements.txt
├── data/                         # Runtime data (gitignored)
│   └── cards.db                  # SQLite database (WAL mode)
├── deploy/
│   ├── setup.sh                  # One-time VM setup (Ubuntu + Oracle Linux)
│   ├── local_setup.sh            # macOS launchd background services
│   ├── studybot.service          # systemd service — bot.py
│   └── studybot-mcp.service      # systemd service — mcp_server.py
└── .github/
    └── workflows/
        └── deploy.yml            # GitHub Actions CI/CD
```

## Notes

- All card data lives in `data/cards.db` — SQLite with WAL mode for safe concurrent access
- The daily digest defaults to 1PM IST — change with `/setdigest HH:MM`
- Interval tuning: `db.py` line 7, `INTERVALS_DAYS`
- Telegram messages route through Telegram's servers — that's how it syncs across your devices


BLOCKED: Use doc-writer agent for documentation changes. Do NOT edit README directly — spawn the doc-writer agent instead.