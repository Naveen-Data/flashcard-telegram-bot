# Project Overview

_Last updated: 2026-08-02 by planner after task: build Study Bot — local Telegram-based Anki-style spaced-repetition flashcard system from scratch_

## Language(s)
- Python: no indicator file yet (new project) — standarts: `.claude/skills/python-code-standarts.md`

## Key Files
| File | Purpose |
|------|---------|
| `db.py` | SQLite database layer — all card and settings CRUD, spaced-repetition scheduling |
| `bot.py` | Async Telegram bot (python-telegram-bot v20+) — commands, background job, callback handler |
| `mcp_server.py` | FastMCP streamable-http server on 127.0.0.1:8811 — exposes db tools to Claude |
| `requirements.txt` | Python dependencies: python-telegram-bot>=20.0, mcp>=1.0 |
| `README.md` | User-facing setup and usage guide (content is fixed verbatim — do not edit) |
| `.gitignore` | Ignores venv/, data/*.db, __pycache__/, .env, *.pyc |
| `data/.gitkeep` | Placeholder to track the data/ directory in git |

## Architecture & Conventions
- All timestamps stored in SQLite as UTC ISO 8601 strings via `datetime.utcnow().isoformat()`
- DB path is always `Path(__file__).parent / "data" / "cards.db"` — never relative string paths
- Module-level constant `INTERVALS_DAYS = [1, 2, 5, 12, 26, 45]` in db.py controls all scheduling
- Single registered chat per installation — stored in `settings` table under key `'chat_id'`
- In-memory `SENT_CARD_IDS: set[int]` in bot.py prevents duplicate pushes within a session
- Callback data format: `"action:card_id"` or `"snooze:type:card_id"` (colon-delimited)
- "add: question / answer" parser uses `" / "` (space-slash-space) as delimiter — preserves "/" in questions
- MCP tools return `{"error": "..."}` when no chat is registered rather than raising

## Do Not Touch
- `README.md`: content is verbatim-specified by the project owner — do not alter a single character

## Known Constraints
- `TELEGRAM_BOT_TOKEN` must come from environment variable — never hardcode
- `db.init_db()` must be called at startup in both `bot.py` and `mcp_server.py` before any db operations
- `snooze_card` computes new due_at from `datetime.utcnow() + delta` — snooze deltas are computed in local time but the resulting offset is timezone-independent
- Stage capped at `len(INTERVALS_DAYS) - 1` (index 5 = 45 days) — do not exceed this
- The `data/` directory is gitignored for `.db` files but the directory itself is tracked via `.gitkeep`
