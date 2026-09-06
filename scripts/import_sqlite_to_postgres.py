#!/usr/bin/env python3
"""One-time SQLite -> PostgreSQL importer for Study Buddy.

Never runs automatically from the bot or MCP server — run by hand, once, after
`alembic upgrade head` has created the schema on the target database.

The single Telegram chat_id in the old database (settings.chat_id) becomes the
one canonical user everything gets attached to. Any *new* user who registers
afterward starts with an empty deck; this script never touches them.

Safety:
- Read-only against the SQLite source (opened with mode=ro).
- Refuses to run twice against the same source file unless --force is passed
  (tracked in the new `import_runs` table by a SHA-256 fingerprint of the file).
- --dry-run performs every step and prints the report, then rolls back —
  nothing is written.
- One transaction: either everything commits, or nothing does.

Usage:
    PGHOST=... PGUSER=... PGPASSWORD=... PGDATABASE=... python scripts/import_sqlite_to_postgres.py \\
        --sqlite-path /home/ubuntu/flashcard-telegram-bot/data/cards.db [--dry-run] [--force]
"""
import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from studybot.db.auth import create_api_token, hash_password  # noqa: E402
from studybot.db.connection import get_connection  # noqa: E402


class _DryRunAbort(Exception):
    """Raised at the very end of a --dry-run to force the transaction to roll back."""


def _dt(value):
    if not value:
        return None
    d = datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite-path", required=True, type=Path, help="Path to the source cards.db")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; write nothing")
    parser.add_argument("--force", action="store_true", help="Re-run even if this exact file was already imported")
    args = parser.parse_args()

    if not args.sqlite_path.exists():
        sys.exit(f"Error: {args.sqlite_path} does not exist.")

    fingerprint = _fingerprint(args.sqlite_path)
    sq = _open_sqlite_readonly(args.sqlite_path)

    report: list[str] = []

    def log(line: str) -> None:
        report.append(line)
        print(line)

    log(f"Source: {args.sqlite_path} (sha256 {fingerprint[:16]}...)")

    try:
        with get_connection() as conn:
            existing_run = conn.execute(
                text("SELECT created_at FROM import_runs WHERE source_fingerprint=:f"), {"f": fingerprint}
            ).scalar()
            if existing_run and not args.force:
                sys.exit(
                    f"This exact file was already imported at {existing_run}. "
                    "Pass --force to re-run anyway (will create duplicate data unless the "
                    "target user already exists and rows are re-inserted with the same IDs)."
                )

            # ---------- preflight: read everything from SQLite first ----------
            chat_id_row = sq.execute("SELECT value FROM settings WHERE key='chat_id'").fetchone()
            if chat_id_row is None:
                sys.exit("No settings.chat_id in the source database — nothing was ever registered. Nothing to import.")
            legacy_chat_id = int(chat_id_row["value"])
            log(f"Legacy Telegram chat_id: {legacy_chat_id}")

            src_cards = sq.execute("SELECT * FROM cards WHERE chat_id=?", (legacy_chat_id,)).fetchall()
            src_reviews = sq.execute("SELECT * FROM review_log WHERE chat_id=?", (legacy_chat_id,)).fetchall()
            src_chat_settings = sq.execute(
                "SELECT * FROM chat_settings WHERE chat_id=?", (legacy_chat_id,)
            ).fetchall()
            src_undo = sq.execute("SELECT * FROM undo_snapshots WHERE chat_id=?", (legacy_chat_id,)).fetchall()
            src_notes = (
                sq.execute("SELECT * FROM session_notes WHERE chat_id=?", (legacy_chat_id,)).fetchall()
                if _table_exists(sq, "session_notes") else []
            )
            src_global_settings = sq.execute("SELECT * FROM settings WHERE key != 'chat_id'").fetchall()
            src_web_auth = (
                sq.execute("SELECT * FROM web_auth WHERE id=1").fetchone()
                if _table_exists(sq, "web_auth") else None
            )

            log(
                f"Found: {len(src_cards)} cards, {len(src_reviews)} review log rows, "
                f"{len(src_chat_settings)} chat settings, {len(src_undo)} undo snapshot(s), "
                f"{len(src_notes)} session notes, {len(src_global_settings)} global settings, "
                f"web_auth {'present' if src_web_auth else 'absent'}."
            )

            # ---------- resolve or create the canonical user ----------
            existing_telegram = conn.execute(
                text("SELECT user_id, id FROM telegram_accounts WHERE telegram_chat_id=:c"),
                {"c": legacy_chat_id},
            ).mappings().first()

            if existing_telegram:
                user_id = existing_telegram["user_id"]
                telegram_account_id = existing_telegram["id"]
                log(f"Reusing existing user_id={user_id} already linked to this Telegram chat.")
            else:
                user_id = conn.execute(
                    text("INSERT INTO users (display_name) VALUES (:n) RETURNING id"),
                    {"n": f"Telegram {legacy_chat_id}"},
                ).scalar_one()
                telegram_account_id = conn.execute(
                    text(
                        "INSERT INTO telegram_accounts (user_id, telegram_user_id, telegram_chat_id)"
                        " VALUES (:u, :tid, :cid) RETURNING id"
                    ),
                    # The old schema only ever recorded the chat id, not a distinct Telegram user id;
                    # using the same value for both is the closest safe approximation. The real
                    # telegram_user_id gets corrected automatically the next time /start runs.
                    {"u": user_id, "tid": legacy_chat_id, "cid": legacy_chat_id},
                ).scalar_one()
                log(f"Created new user_id={user_id}, telegram_account_id={telegram_account_id}.")

            # ---------- cards (two-pass: insert, then wire up reverse_of) ----------
            for row in src_cards:
                conn.execute(
                    text(
                        "INSERT INTO cards (id, user_id, question, answer, stage, due_at, created_at, tags,"
                        " ease_factor, interval_days, repetitions, card_type, image_file_id, consecutive_again,"
                        " stability, difficulty, last_review, notes, suspended, buried_until, reverse_of)"
                        " VALUES (:id, :uid, :q, :a, :stage, :due, :created, :tags, :ef, :iv, :rep, :ctype,"
                        " :img, :ca, :stab, :diff, :lr, :notes, :susp, :buried, NULL)"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": row["id"], "uid": user_id, "q": row["question"], "a": row["answer"],
                        "stage": row["stage"], "due": _dt(row["due_at"]), "created": _dt(row["created_at"]),
                        "tags": row["tags"], "ef": row["ease_factor"], "iv": row["interval_days"],
                        "rep": row["repetitions"], "ctype": row["card_type"], "img": row["image_file_id"],
                        "ca": row["consecutive_again"], "stab": row["stability"], "diff": row["difficulty"],
                        "lr": _dt(row["last_review"]), "notes": row["notes"],
                        "susp": bool(row["suspended"]), "buried": _dt(row["buried_until"]),
                    },
                )
            for row in src_cards:
                if row["reverse_of"] is not None:
                    conn.execute(
                        text("UPDATE cards SET reverse_of=:r WHERE id=:id AND user_id=:uid"),
                        {"r": row["reverse_of"], "id": row["id"], "uid": user_id},
                    )
            log(f"Imported {len(src_cards)} cards.")

            # ---------- review log (ids preserved so undo_snapshots can reference them) ----------
            for row in src_reviews:
                conn.execute(
                    text(
                        "INSERT INTO review_log (id, user_id, card_id, quality, reviewed_at)"
                        " VALUES (:id, :uid, :cid, :q, :at) ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": row["id"], "uid": user_id, "cid": row["card_id"], "q": row["quality"],
                     "at": _dt(row["reviewed_at"])},
                )
            log(f"Imported {len(src_reviews)} review log rows.")

            # ---------- per-chat settings -> per-user settings ----------
            for row in src_chat_settings:
                conn.execute(
                    text(
                        "INSERT INTO user_settings (user_id, key, value) VALUES (:u, :k, :v)"
                        " ON CONFLICT (user_id, key) DO UPDATE SET value=:v"
                    ),
                    {"u": user_id, "k": row["key"], "v": row["value"]},
                )
            log(f"Imported {len(src_chat_settings)} per-user settings.")

            # ---------- undo snapshot (at most one, PK is now user_id) ----------
            for row in src_undo:
                conn.execute(
                    text(
                        "INSERT INTO undo_snapshots (user_id, card_id, ease_factor, interval_days, repetitions,"
                        " due_at, stage, consecutive_again, review_log_id, created_at, stability, difficulty, last_review)"
                        " VALUES (:u, :cid, :ef, :iv, :rep, :due, :stage, :ca, :log, :created, :stab, :diff, :lr)"
                        " ON CONFLICT (user_id) DO NOTHING"
                    ),
                    {
                        "u": user_id, "cid": row["card_id"], "ef": row["ease_factor"], "iv": row["interval_days"],
                        "rep": row["repetitions"], "due": _dt(row["due_at"]), "stage": row["stage"],
                        "ca": row["consecutive_again"], "log": row["review_log_id"], "created": _dt(row["created_at"]),
                        "stab": row["stability"], "diff": row["difficulty"], "lr": _dt(row["last_review"]),
                    },
                )
            log(f"Imported {len(src_undo)} undo snapshot(s).")

            # ---------- session notes ----------
            for row in src_notes:
                conn.execute(
                    text(
                        "INSERT INTO session_notes (id, user_id, telegram_account_id, topic, content, tags, created_at)"
                        " VALUES (:id, :u, :tg, :topic, :content, :tags, :created) ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": row["id"], "u": user_id, "tg": telegram_account_id, "topic": row["topic"],
                     "content": row["content"], "tags": row["tags"], "created": _dt(row["created_at"])},
                )
            log(f"Imported {len(src_notes)} session notes.")

            # ---------- global app settings (digest_time_ist etc.) ----------
            for row in src_global_settings:
                conn.execute(
                    text(
                        "INSERT INTO app_settings (key, value) VALUES (:k, :v)"
                        " ON CONFLICT (key) DO UPDATE SET value=:v"
                    ),
                    {"k": row["key"], "v": row["value"]},
                )
            log(f"Imported {len(src_global_settings)} global app settings.")

            # ---------- fix up sequences so future inserts don't collide with preserved ids ----------
            for table in ("cards", "review_log", "session_notes"):
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                    f" GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1))"
                ))

            # ---------- password: never reuse the old PBKDF2 hash, issue a fresh one ----------
            new_password = None
            has_identity = conn.execute(
                text("SELECT 1 FROM user_identities WHERE user_id=:u AND provider='password'"), {"u": user_id}
            ).first()
            if src_web_auth and not has_identity:
                import secrets as _secrets
                new_password = _secrets.token_urlsafe(12)
                conn.execute(
                    text(
                        "INSERT INTO user_identities (user_id, provider, provider_subject, password_hash)"
                        " VALUES (:u, 'password', :sub, :hash)"
                    ),
                    {"u": user_id, "sub": src_web_auth["username"], "hash": hash_password(new_password)},
                )
                log(
                    f"Migrated web login for username '{src_web_auth['username']}' with a NEW password "
                    f"(the old PBKDF2 hash cannot be reused under Argon2): {new_password}\n"
                    "  >>> Log in with this once, then change it — there is no other record of it. <<<"
                )
            elif src_web_auth and has_identity:
                log("A password login already exists for this user on the target — left untouched.")
            else:
                log("No web_auth row in the source — this user has no password login yet (fine, MCP/Telegram still work).")

            # ---------- verification ----------
            pg_cards = conn.execute(text("SELECT COUNT(*) FROM cards WHERE user_id=:u"), {"u": user_id}).scalar_one()
            pg_reviews = conn.execute(
                text("SELECT COUNT(*) FROM review_log WHERE user_id=:u"), {"u": user_id}
            ).scalar_one()
            pg_settings = conn.execute(
                text("SELECT COUNT(*) FROM user_settings WHERE user_id=:u"), {"u": user_id}
            ).scalar_one()

            mismatches = []
            if pg_cards < len(src_cards):
                mismatches.append(f"cards: expected >= {len(src_cards)}, got {pg_cards}")
            if pg_reviews < len(src_reviews):
                mismatches.append(f"review_log: expected >= {len(src_reviews)}, got {pg_reviews}")
            if pg_settings < len(src_chat_settings):
                mismatches.append(f"user_settings: expected >= {len(src_chat_settings)}, got {pg_settings}")

            if mismatches:
                sys.exit("Verification FAILED, rolling back:\n  " + "\n  ".join(mismatches))

            log(f"Verified: user {user_id} now has {pg_cards} cards, {pg_reviews} review rows, {pg_settings} settings.")

            if not args.dry_run:
                conn.execute(
                    text("INSERT INTO import_runs (source_fingerprint) VALUES (:f) ON CONFLICT DO NOTHING"),
                    {"f": fingerprint},
                )
                log("Recorded this import in import_runs — re-running the same file will require --force.")
            else:
                log("--dry-run: rolling back, nothing was written.")
                raise _DryRunAbort()

    except _DryRunAbort:
        pass
    finally:
        sq.close()

    if not args.dry_run:
        _, raw_token = create_api_token(user_id, "Migrated MCP access")
        print(
            f"\nMCP token for user {user_id} (save this now, it is never shown again):\n  {raw_token}"
        )
        print("\nDone.")
    else:
        print("\nDry run complete — no changes made.")


if __name__ == "__main__":
    main()
