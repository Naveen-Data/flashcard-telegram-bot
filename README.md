# Study Buddy — Telegram Spaced Repetition Bot

A personal study companion that lives in Telegram. Add flashcards, get notified when they're due, review with FSRS spaced repetition, track retention, and let Claude create and maintain cards directly via MCP.

## Features

- **FSRS scheduling** — models memory per card (stability + difficulty) rather than one global ease factor
- **Study window** — cards come due when you actually study, not at whatever hour you last reviewed
- **Daily cap** — stops a backlog day turning into a 60-card slog
- **Exam mode** — compresses intervals for a tag so nothing is scheduled past your deadline
- **Retention stats** — true recall rate, broken down weakest-tag-first
- **Forecast** — see the next 7 days of review load before it hits
- **Suspend / bury** — park a card indefinitely, or skip it for today without touching its schedule
- **Cloze, image, and reverse cards** — beyond plain question/answer
- **Leech detection** — flags cards you keep failing so you can rewrite or park them
- **Streaks, daily goals, digest, weekly report, pomodoro, snooze, search**
- **MCP server** — Claude can create *and maintain* your deck: edit, delete, suspend, search, and read your retention data

---

## Commands

### Adding cards

| Input | Result |
|---|---|
| `add: question / answer` | Basic card |
| `add: question / answer #tag1 #tag2` | With tags |
| `add: term / definition --both` | Also creates the reverse card, scheduled independently |
| `cloze: The {{c1::term}} is hidden` | Cloze deletion card |
| Photo + caption `add: q / a` | Card with an attached image |

### Managing cards

| Command | Description |
|---|---|
| `/edit <id> question / answer` | Edit a card — scheduling state is preserved |
| `/note <id> text` | Attach a note, shown only after you reveal the answer |
| `/delete <id>` | Delete permanently, along with its review history |
| `/card <id>` | Details, memory state, and recent review history |
| `/suspend <id>` · `/unsuspend <id>` | Take a card out of rotation / bring it back |
| `/suspended` | List suspended cards |
| `/bury <id>` | Hide until tomorrow; schedule untouched |

Card IDs are shown on every card during review, and in `/search` results.

### Review

| Command | Description |
|---|---|
| `/review` | Review due cards |
| `/review rag` | Only `#rag` cards |
| `/review all` | Ignore due dates |
| `/review force` | Ignore your daily cap |
| `/undo` | Undo your last answer, restoring full memory state |
| `/tags` | All tags with card and due counts |
| `/search keyword` | Search questions and answers |
| `/leeches` | Cards you keep getting wrong |

### Insight

| Command | Description |
|---|---|
| `/forecast` | Due-card load for the next 7 days, flagging days over your cap |
| `/stats` | True retention, overall and per tag |
| `/streak` | Current streak, longest, today's goal progress |

### Settings

| Command | Description |
|---|---|
| `/goal 10` | Daily card target (aspiration) |
| `/cap 20` · `/cap off` | Max cards surfaced per day (ceiling) |
| `/window 21:00-23:00` · `/window off` | When cards come due (IST) |
| `/retention 0.9` | Target recall probability, 0.70–0.99 |
| `/exam rag 2026-08-20` | Cram schedule for a tag before a deadline |
| `/exam list` · `/exam clear rag` | Manage exams |
| `/setdigest 14:00` | Daily digest time (IST) |
| `/dnd 22:00-07:00` · `/dnd off` | Quiet hours — suppresses due-card pings |
| `/backup` | Export all cards as a JSON document |

### Focus

| Command | Description |
|---|---|
| `/pomodoro` | 25 min focus timer |
| `/pomodoro 45` | Custom timer in minutes |

---

## Scheduling

### FSRS

Implemented in [`studybot/fsrs.py`](studybot/fsrs.py) using FSRS-5 with the published default weights.

Where SM-2 kept a single `ease_factor` per card and multiplied intervals by it, FSRS tracks two values:

| Variable | Meaning |
|---|---|
| **Stability** | Days until recall probability decays to your target retention |
| **Difficulty** | Intrinsic hardness of the card, 1–10 |

Two things this buys you over SM-2:

- **Elapsed time counts.** A review two days late is treated differently from one on time. SM-2 ignored this entirely.
- **Lapses keep partial credit.** Failing a card with 30 days of stability drops it to roughly 3.6 days — not back to day one. Prior learning still counts for something.

The review buttons are unchanged:

| Button | FSRS rating | Effect |
|---|---|---|
| 🔴 Again | 1 | Stability decays, difficulty rises, card returns later in the session |
| 🟠 Hard | 2 | Small stability gain, difficulty rises slightly |
| 🟢 Good | 3 | Normal stability gain |
| 🔵 Easy | 4 | Large stability gain, difficulty falls |

**Target retention** (`/retention`) trades review volume against recall. At 0.95 you see cards more often and forget less; at 0.80 you review far less and accept more forgetting. Default is 0.90.

**Migration is automatic.** Existing SM-2 cards are seeded on startup — `interval_days` becomes stability, `ease_factor` maps inversely onto difficulty (EF 2.5 → D 3, EF 1.3 → D 10). Cards never reviewed are left untouched so they initialise properly from their first real answer.

Per-user weight optimisation is not implemented — it needs torch/scipy, which is too heavy for a 1GB VM. The published defaults are trained on a large public dataset and are strong on their own.

### Study window

Without a window, a card comes due at whatever hour you happened to review it. Study at midnight and your cards will surface at midnight forever — invisible during the day, then all at once late at night.

`/window 21:00-23:00` snaps new due times into that window and fans cards out across it deterministically, so a batch reviewed together doesn't all land on the same minute.

### Exam mode

`/exam rag 2026-08-20` caps intervals for `#rag` cards at half the remaining runway, so you always get at least one more look before the date, with reviews naturally bunching as it approaches. Once the date passes, normal scheduling resumes.

### Retention stats

`/stats` reports **true retention**: how often you recalled a card you had *already learned*. Each card's first-ever review is excluded — it measures whether you happened to know the material already, not whether the system is working. The per-tag breakdown is sorted weakest-first, which is usually the fastest way to see what actually needs re-teaching.

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
set -a && source .env && set +a && python bot.py
```

```bash
set -a && source .env && set +a && python mcp_server.py
```

Send `/start` to your bot in Telegram to register your chat.

> Only one bot instance may poll a given token at a time. If a local run and the VM are both up, Telegram returns `Conflict: terminated by other getUpdates request` and neither responds reliably — stop one of them.

### 4. Run as background services (Mac)

Installs both as launchd agents — auto-start on login, no terminal needed:

```bash
bash deploy/local_setup.sh
```

```bash
tail -f logs/bot.log
tail -f logs/mcp.log
```

```bash
launchctl unload ~/Library/LaunchAgents/com.studybot.bot.plist
launchctl load ~/Library/LaunchAgents/com.studybot.bot.plist
```

---

## Cloud Deployment (Oracle Cloud Always Free)

Runs 24/7 on an Oracle Cloud Always Free VM (Ubuntu 22.04, VM.Standard.E2.1.Micro).

### 1. Create the VM

- Image: Ubuntu 22.04
- Shape: VM.Standard.E2.1.Micro (Always Free)
- Upload your SSH public key during creation

### 2. Open ports

On the VM (`ufw` is often absent on the minimal image):

```bash
sudo iptables -I INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 8811 -j ACCEPT
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

Then add matching ingress rules in the VCN security list. Set **Destination Port Range** — leaving source port set instead is a common mistake that silently blocks traffic.

| Protocol | Destination port | Purpose |
|---|---|---|
| TCP | 22 | SSH |
| TCP | 80 | HTTP / Let's Encrypt challenge |
| TCP | 443 | HTTPS |
| TCP | 8811 | MCP server (direct) |

### 3. One-time VM setup

```bash
ssh ubuntu@<VM_IP>
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Naveen-Data/flashcard-telegram-bot.git
cd flashcard-telegram-bot
bash deploy/setup.sh
```

This installs Python, creates the venv, and registers `studybot` and `studybot-mcp` as systemd services.

### 4. GitHub Secrets

Repo → Settings → Secrets → Actions:

| Secret | Value |
|---|---|
| `ORACLE_HOST` | VM public IP |
| `ORACLE_USER` | `ubuntu` |
| `ORACLE_SSH_KEY` | Contents of your private SSH key |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `MCP_HOST` | `0.0.0.0` so the MCP server binds publicly |

### 5. Deploy

Every push to `main` triggers GitHub Actions, which pulls, installs, rewrites `.env`, and restarts both services.

```bash
git push origin main
```

### HTTPS (required for some MCP clients)

nginx reverse-proxies `8811` behind a Let's Encrypt certificate, using [nip.io](https://nip.io) for a hostname since the VM has no domain:

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d <VM_IP>.nip.io --non-interactive --agree-tos -m you@example.com
```

The MCP server is then reachable at `https://<VM_IP>.nip.io/mcp`.

### Useful VM commands

```bash
sudo systemctl status studybot studybot-mcp
sudo journalctl -u studybot -f
sudo journalctl -u studybot-mcp -f
```

---

## MCP Integration (Claude)

Claude can create *and maintain* your deck — not just add to it.

### Connect

```bash
claude mcp add study-bot --transport http https://<VM_IP>.nip.io/mcp --scope user
```

```bash
claude mcp add study-bot --transport http http://127.0.0.1:8811/mcp --scope user
```

> The MCP server currently runs **without authentication** — anyone with the URL can read and write your deck. Fine for a personal bot on an obscure host; add auth before sharing the URL.

### Available tools

| Tool | Description |
|---|---|
| `add_card(question, answer, tags, notes, reverse)` | Add a card, optionally with a note and its reverse |
| `add_cards_bulk(cards)` | Add many at once |
| `edit_card(card_id, ...)` | Update fields in place; scheduling state preserved |
| `delete_card(card_id)` | Delete permanently |
| `suspend_card(card_id, suspended)` | Park or restore a card |
| `search_cards(keyword)` | Find existing cards — use before adding near-duplicates |
| `list_due_cards()` | Cards currently due |
| `get_card_history(card_id)` | Full detail plus review log |
| `get_stats()` | Deck totals |
| `get_retention_stats(days)` | True retention, with per-tag breakdown |
| `get_forecast(days)` | Upcoming review load |
| `get_weak_cards()` | Leeches and cards FSRS rates as hard |

### Writing good cards

- Question under 300 characters, answer under 500 — Telegram caps messages at 4096
- One idea per card. For an "X vs Y vs Z" comparison, emit one card per item rather than cramming all three into one answer
- Prefer "apply it" over "define it" — test recall, not recognition
- Use `notes` for the *why* behind a fact, so the answer itself stays short
- Use `reverse=True` for term/definition pairs; skip it for one-way facts

---

## Project Structure

```
├── bot.py                        # thin entrypoint -> studybot.bot.app
├── mcp_server.py                 # thin entrypoint -> studybot.mcp.server
├── requirements.txt
├── studybot/
│   ├── fsrs.py                   # FSRS-5 algorithm, leech threshold
│   ├── scheduling.py             # study window, exam clamping, bury timing
│   ├── db/
│   │   ├── connection.py         # schema, migrations, FSRS backfill
│   │   ├── cards.py              # card CRUD, review recording, forecast
│   │   ├── settings.py           # global + per-chat settings, streaks
│   │   └── reviews.py            # review log, retention stats, undo, backup
│   ├── bot/
│   │   ├── app.py                # handler + job registration, main()
│   │   ├── commands.py           # command handlers
│   │   ├── callbacks.py          # review flow, inline buttons
│   │   ├── jobs.py               # due checks, daily digest, weekly report
│   │   ├── keyboards.py          # inline keyboards
│   │   └── utils.py              # clipping, progress bars, IST, cloze parsing
│   └── mcp/
│       └── server.py             # MCP tool definitions
├── data/                         # runtime, gitignored — cards.db
├── deploy/
│   ├── setup.sh                  # one-time VM setup
│   ├── local_setup.sh            # macOS launchd services
│   ├── studybot.service          # systemd — bot
│   └── studybot-mcp.service      # systemd — MCP server
└── .github/workflows/deploy.yml  # CI/CD
```

## Notes

- All card data lives in `data/cards.db` — SQLite in WAL mode, so the bot and MCP server can both use it safely
- Schema changes are applied automatically at startup by `init_db()`; adding a column is a matter of extending its migration list
- Scheduling behaviour is tuned in `studybot/fsrs.py` (weights, retention floor, max interval) and `studybot/scheduling.py` (window, exam clamping)
- The daily digest defaults to 1PM IST — `/setdigest HH:MM` to change it
- Telegram messages route through Telegram's servers; that's how they sync across your devices
