"""Single-account login for the web app. One account only — this is a personal
deck, not a multi-tenant product, so "register" bootstraps the one account and
is refused once it exists.

Passwords: PBKDF2-HMAC-SHA256, 200k iterations, random salt per account
(stdlib `hashlib` — no bcrypt/argon2 dependency for one password check).
Sessions: opaque random tokens, stored server-side with a 30-day expiry,
checked with a constant-time comparison to avoid timing leaks.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from studybot.db.connection import get_connection

_PBKDF2_ITERATIONS = 200_000
_SESSION_TTL = timedelta(days=30)


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS).hex()


def account_exists() -> bool:
    with get_connection() as conn:
        return conn.execute("SELECT 1 FROM web_auth WHERE id=1").fetchone() is not None


def register(username: str, password: str) -> str:
    """Creates the one account. Raises ValueError if it already exists."""
    username = username.strip()
    if not username or not password:
        raise ValueError("username and password are required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM web_auth WHERE id=1").fetchone():
            raise ValueError("an account already exists")
        salt = os.urandom(16)
        conn.execute(
            "INSERT INTO web_auth (id, username, salt, password_hash, created_at) VALUES (1,?,?,?,?)",
            (username, salt.hex(), _hash_password(password, salt), datetime.utcnow().isoformat()),
        )
        conn.commit()
    return _create_session()


def login(username: str, password: str) -> Optional[str]:
    """Returns a new session token, or None if the credentials are wrong."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM web_auth WHERE id=1").fetchone()
    if row is None or not hmac.compare_digest(row["username"], username.strip()):
        return None
    salt = bytes.fromhex(row["salt"])
    if not hmac.compare_digest(_hash_password(password, salt), row["password_hash"]):
        return None
    return _create_session()


def _create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO web_sessions (token, created_at, expires_at) VALUES (?,?,?)",
            (token, now.isoformat(), (now + _SESSION_TTL).isoformat()),
        )
        conn.commit()
    return token


def verify_session(token: str) -> bool:
    if not token:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT expires_at FROM web_sessions WHERE token=?", (token,)
        ).fetchone()
    return row is not None and datetime.utcnow().isoformat() <= row["expires_at"]


def logout(token: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM web_sessions WHERE token=?", (token,))
        conn.commit()
