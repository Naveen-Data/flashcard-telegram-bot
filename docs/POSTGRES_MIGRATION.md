# PostgreSQL Multi-User Migration

Runbook for the completed migration from single-user SQLite to multi-user PostgreSQL.
Covers architecture, auth, deployment, the one-time data import, and how to inspect
the production database.

## 1. Architecture

```
users
 ├── user_identities      (provider='password', unique per user; Argon2id hash)
 ├── telegram_accounts    (one Telegram chat linked to one user)
 ├── api_tokens           (personal MCP bearer tokens)
 └── web_sessions         (browser login sessions)

cards, review_log, user_settings, undo_snapshots, session_notes
 └── all carry user_id, FK'd to users(id) ON DELETE CASCADE

app_settings
 └── global key/value, not scoped to any user (e.g. digest_time_ist)
```

Every per-user table filters by `user_id` in the SQL `WHERE` clause itself
(`studybot/db/*.py`), not by fetching rows globally and checking `user_id` in Python.
There is no code path that can read another user's data by mistake — the query
never returns rows outside the caller's scope in the first place.

`app_settings` is the only table with no `user_id` column; it holds instance-wide
configuration and is legitimately shared.

Schema source of truth: `alembic/versions/0001_postgres_multi_user.py`. Foreign
keys cascade on delete, so deleting a `users` row removes all of that user's cards,
reviews, tokens, and sessions.

## 2. MCP authentication

There is no shared `MCP_AUTH_TOKEN` anymore. Every caller — Telegram bot, MCP
client, browser — is a specific, revocable principal:

- `/mcp` — personal API tokens, created per-user in the web app's Tokens tab.
  Verified by `studybot.db.authenticate_api_token`.
- `/api/*` — browser session tokens issued at login. Verified by
  `studybot.db.authenticate_session`.

Both paths converge on `_RequireAuth`, an ASGI middleware in `studybot/mcp/server.py`
that runs before any route handler or MCP tool:

1. Reads the `Authorization: Bearer <token>` header.
2. Resolves it to a `user_id` via the matching `authenticate_*` function.
3. Stashes it once at `scope["state"]["study_user_id"]`.
4. Returns `401` if the token is missing, unknown, expired, or revoked.

Tool functions and route handlers read `study_user_id` from request state —
they never re-parse or re-trust anything the client sends in the call body.

`/api/auth/register`, `/api/auth/login`, `/api/auth/logout` are the only
unauthenticated routes (someone has to be able to log in before holding a
token). `/api/auth/change-password` requires a session like any other `/api/`
route.

### Token storage scheme (`studybot/db/auth.py`)

- Passwords: Argon2id (`argon2.PasswordHasher`), rehashed transparently on
  login if parameters are outdated.
- Bearer tokens (both session and API tokens): the raw value is a
  `secrets.token_urlsafe(32)` string, shown to the user exactly once at
  creation. Only a SHA-256 digest of it is stored (`token_hash`, `BYTEA`),
  plus a 12-character prefix (`token_prefix`) for indexed lookup.
- Verification looks up candidate rows by prefix, then compares the full hash
  with `hmac.compare_digest` to avoid timing side-channels. The raw token is
  never persisted or logged anywhere.

## 3. What changed

| Area | Files | Change |
|---|---|---|
| Schema | `alembic/versions/0001_postgres_multi_user.py`, `alembic.ini` | New Postgres schema: `users`, `user_identities`, `telegram_accounts`, `api_tokens`, `web_sessions`, `import_runs`, plus `user_id` added to `cards`, `review_log`, `user_settings`, `undo_snapshots`, `session_notes` |
| DB layer | `studybot/db/connection.py`, `cards.py`, `reviews.py`, `notes.py`, `settings.py`, `auth.py`, `users.py` (new), `__init__.py` | SQLAlchemy + `psycopg` instead of the raw `sqlite3` connection; every query takes and filters by `user_id`; `auth.py` rewritten for Argon2id + hashed bearer tokens |
| Bot | `studybot/bot/commands.py`, `callbacks.py`, `jobs.py`, `studybot/review.py` | Resolve `user_id` from the Telegram chat via `telegram_accounts` before touching any card/review data |
| Web / MCP | `studybot/mcp/server.py`, `studybot/web.py` | `_RequireAuth` middleware, per-user token endpoints (`/api/tokens`), auth endpoints (`/api/auth/*`), inline Tokens tab in the `/app` UI |
| Migration script | `scripts/import_sqlite_to_postgres.py` (new) | One-time SQLite -> Postgres importer, see section 5 |
| Tests | `tests/test_web.py` | Rewritten as a Postgres-backed integration check; requires `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` |
| Deploy | `.github/workflows/deploy.yml`, `deploy/setup.sh`, `deploy/check_auth_token.sh`, `deploy/studybot-mcp.service` (new) | `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`/`PGSSLMODE` replace `MCP_AUTH_TOKEN`; `alembic upgrade head` runs before service restart |

## 4. Environment variables

`studybot/db/connection.py`'s `build_database_url()` assembles the connection
URL from these discrete component vars via SQLAlchemy's `URL.create()`; there
is no single connection-string variable. If any required var is unset it
raises `RuntimeError: Missing required env vars: ...` naming exactly which of
`PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` are missing.

| Variable | Required | Notes |
|---|---|---|
| `PGHOST` | Yes | Postgres host. |
| `PGUSER` | Yes | Postgres role name. |
| `PGPASSWORD` | Yes | Postgres role password. |
| `PGDATABASE` | Yes | Database name. |
| `PGPORT` | Optional | Defaults to `5432`. |
| `PGSSLMODE` | Optional | Defaults to `require`. |
| `MCP_AUTH_TOKEN` | Removed | No longer read anywhere. Delete the `MCP_AUTH_TOKEN` GitHub Actions secret. |
| `TELEGRAM_BOT_TOKEN` | Yes | Unchanged. |
| `MCP_HOST` | Optional | Unchanged. |
| `WEB_ALLOWED_ORIGINS` | Optional | Unchanged, comma-separated CORS origins for the deployed frontend. |

`.github/workflows/deploy.yml` passes these through as GitHub Actions secrets:
set `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, and `PGSSLMODE`
as repository secrets. Delete any leftover single-connection-string secret from
an earlier version of this migration if one still exists.

## 5. Production migration procedure

Run in order, against the target VM's `.env` (which must already have
`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, and `PGSSLMODE`
pointing at the Postgres instance):

```bash
cd ~/flashcard-telegram-bot
source venv/bin/activate
export PGHOST=<pg-host>
export PGPORT=5432
export PGUSER=studybot
export PGPASSWORD=<password>
export PGDATABASE=studybot
export PGSSLMODE=require

# 1. Create the schema
alembic upgrade head

# 2. Dry run — reports what would be imported, writes nothing
python scripts/import_sqlite_to_postgres.py \
  --sqlite-path /home/ubuntu/flashcard-telegram-bot/data/cards.db --dry-run

# 3. Real run
python scripts/import_sqlite_to_postgres.py \
  --sqlite-path /home/ubuntu/flashcard-telegram-bot/data/cards.db
```

The real run prints an MCP token at the end:

```text
MCP token for user <id> (save this now, it is never shown again):
  <raw token>
```

Copy it immediately — only its SHA-256 hash is stored, so it cannot be
recovered afterward. If lost, create a new one from the web app's Tokens tab.

Re-running against the same file without `--force` exits early (fingerprint
already recorded in `import_runs`); `--force` re-runs but relies on
`ON CONFLICT (id) DO NOTHING` for cards/review_log/session_notes, so it will
not duplicate rows that were already imported under the same ids.

## 6. Known gap: old web login password

The old SQLite `web_auth` table stored passwords as PBKDF2-HMAC-SHA256. The
new schema only supports Argon2id, and the two hash formats are not
interchangeable. The importer does not attempt to migrate the old hash — it:

1. Detects a `web_auth` row in the source with no existing `password` identity
   for the target user.
2. Generates a new random password (`secrets.token_urlsafe(12)`).
3. Stores its Argon2id hash and prints the plaintext once in the run log.

After migration, log in once with that printed password, then immediately
call the existing endpoint to set a real one:

```bash
curl -X POST https://<host>/api/auth/change-password \
  -H "Authorization: Bearer <session-token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "<printed-password>", "new_password": "<new-password>"}'
```

## 7. Verifying the migration

Compare row counts against the original file:

```bash
sqlite3 /home/ubuntu/flashcard-telegram-bot/data/cards.db \
  "SELECT COUNT(*) FROM cards; SELECT COUNT(*) FROM review_log;"
```

Against those printed by the importer's own verification step (`Verified: user
<id> now has N cards, M review rows, ...`), and optionally directly in Postgres:

```sql
SELECT COUNT(*) FROM cards WHERE user_id = <id>;
SELECT COUNT(*) FROM review_log WHERE user_id = <id>;
```

Then run the integration test suite against a scratch database (not
production — the tests create real users/tokens):

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=user
export PGPASSWORD=<password>
export PGDATABASE=studybot_test
export PGSSLMODE=disable

alembic upgrade head
python tests/test_web.py
```

## 8. Day-to-day: MCP tokens

Token lifecycle is managed entirely through the web app — no separate admin
tool or CLI. Log in at `/app`, open the **Tokens** tab (built into
`studybot/web.py`'s inline HTML), then:

- **Create**: "+ New MCP token", give it a name. The raw token is shown once;
  copy it into the MCP client config immediately.
- **Revoke**: delete button next to a token. Takes effect immediately —
  `authenticate_api_token` only matches non-revoked, non-expired rows.

## 9. Inspecting Postgres via DBeaver over an SSH tunnel

Port 5432 on the Postgres VM is restricted to the app VM's public IP only.
The two VMs are in different Oracle Cloud tenancies with no private VCN
connectivity, so connect through the app VM as a jump host:

```bash
ssh -L 5433:<pg-host>:5432 ubuntu@<app-vm-ip>
```

Keep that session open, then in DBeaver create a PostgreSQL connection to
`localhost:5433` with the normal database credentials. Traffic goes:
DBeaver -> localhost:5433 -> SSH tunnel -> app VM -> `<pg-host>:5432`.

Postgres VM networking (5432 restricted to the app VM's public IP, TLS via
`sslmode=require`) is assumed already configured; not covered here.

## 10. Rollback plan

The importer never writes to the source SQLite file (opened `mode=ro`), so
rollback does not need to touch it.

To roll back a bad deploy:

1. Redeploy the previous commit (`git checkout <previous-sha>` on the VM, or
   re-run the GitHub Actions deploy against that ref).
2. Restore the previous `.env` (the one without `PGHOST`/`PGUSER`/
   `PGPASSWORD`/`PGDATABASE`, with `MCP_AUTH_TOKEN` if that's what the
   previous deploy expected).
3. Only if reverting the entire Postgres deploy: `alembic downgrade -1` drops
   every table this migration created, via `CASCADE`. This is destructive and
   only appropriate when abandoning the whole multi-user schema, not for
   undoing a single bad import run.

## 11. Open assumptions

- The migrated user's password is reset, not preserved (see section 6). This
  is a deliberate choice, not a bug — flag to anyone relying on the old
  password still working.
- `telegram_accounts.telegram_user_id` is set equal to `telegram_chat_id` for
  the migrated account, because the old schema never recorded a distinct
  Telegram user id. This is an approximation; it self-corrects the next time
  `/start` runs against the real Telegram API.
- Postgres VM networking (port 5432 restricted to the app VM's public IP,
  TLS via `sslmode=require`) is assumed already set up and is out of scope
  for this document.
