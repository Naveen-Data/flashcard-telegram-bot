"""Multi-user Argon2id authentication with hash-only bearer storage."""
import hashlib, hmac, secrets
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from studybot.db.connection import get_connection

_hasher, _ttl = PasswordHasher(), timedelta(days=30)
def _now(): return datetime.now(timezone.utc)
def _raw(): return secrets.token_urlsafe(32)
def _prefix(value): return value[:12]
def _hash(value): return hashlib.sha256(value.encode()).digest()
def _username(value):
    value = value.strip().lower()
    if not value: raise ValueError("username is required")
    return value
def _session(conn, user_id):
    raw, now = _raw(), _now()
    conn.execute(text("INSERT INTO web_sessions (user_id,token_prefix,token_hash,created_at,expires_at) VALUES (:u,:p,:h,:c,:e)"), {"u":user_id,"p":_prefix(raw),"h":_hash(raw),"c":now,"e":now+_ttl})
    return raw

def hash_password(password: str) -> str: return _hasher.hash(password)

def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    if len(new_password) < 8: raise ValueError("password must be at least 8 characters")
    with get_connection() as conn:
        row = conn.execute(text("SELECT password_hash FROM user_identities WHERE user_id=:u AND provider='password'"), {"u":user_id}).mappings().first()
        if not row: return False
        try: ok = _hasher.verify(row["password_hash"], old_password)
        except (InvalidHashError, VerifyMismatchError): ok = False
        if not ok: return False
        conn.execute(text("UPDATE user_identities SET password_hash=:h,updated_at=now() WHERE user_id=:u AND provider='password'"), {"h":_hasher.hash(new_password),"u":user_id})
        return True

def register_user(username: str, password: str, display_name: str | None = None) -> tuple[int, str]:
    username = _username(username)
    if len(password) < 8: raise ValueError("password must be at least 8 characters")
    try:
        with get_connection() as conn:
            uid = conn.execute(text("INSERT INTO users (display_name) VALUES (:n) RETURNING id"), {"n":display_name}).scalar_one()
            conn.execute(text("INSERT INTO user_identities (user_id,provider,provider_subject,password_hash) VALUES (:u,'password',:n,:h)"), {"u":uid,"n":username,"h":_hasher.hash(password)})
            return int(uid), _session(conn, uid)
    except IntegrityError as exc: raise ValueError("username is already registered") from exc

def login(username: str, password: str) -> str | None:
    try: username = _username(username)
    except ValueError: return None
    with get_connection() as conn:
        row = conn.execute(text("SELECT user_id,password_hash FROM user_identities WHERE provider='password' AND provider_subject=:n"), {"n":username}).mappings().first()
        if not row: return None
        try: ok = _hasher.verify(row["password_hash"], password)
        except (InvalidHashError, VerifyMismatchError): ok = False
        if not ok: return None
        if _hasher.check_needs_rehash(row["password_hash"]): conn.execute(text("UPDATE user_identities SET password_hash=:h,updated_at=now() WHERE user_id=:u AND provider='password'"), {"h":_hasher.hash(password),"u":row["user_id"]})
        return _session(conn, row["user_id"])

def _authenticate(raw: str, table: str) -> int | None:
    if not raw: return None
    with get_connection() as conn:
        rows = conn.execute(text(f"SELECT id,user_id,token_hash FROM {table} WHERE token_prefix=:p AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>:n)"), {"p":_prefix(raw),"n":_now()}).mappings()
        for row in rows:
            if hmac.compare_digest(bytes(row["token_hash"]), _hash(raw)):
                conn.execute(text(f"UPDATE {table} SET last_used_at=:n WHERE id=:i"), {"n":_now(),"i":row["id"]})
                return int(row["user_id"])
    return None
def authenticate_session(raw_token: str) -> int | None: return _authenticate(raw_token, "web_sessions")
def logout(raw_token: str) -> None:
    if raw_token:
        with get_connection() as c: c.execute(text("UPDATE web_sessions SET revoked_at=:n WHERE token_prefix=:p AND token_hash=:h AND revoked_at IS NULL"), {"n":_now(),"p":_prefix(raw_token),"h":_hash(raw_token)})
def create_api_token(user_id: int, name: str, expires_at: datetime | None = None) -> tuple[dict, str]:
    if not name.strip(): raise ValueError("token name is required")
    raw = _raw()
    with get_connection() as c:
        row=c.execute(text("INSERT INTO api_tokens (user_id,name,token_prefix,token_hash,expires_at) VALUES (:u,:n,:p,:h,:e) RETURNING id,name,token_prefix,created_at,expires_at,last_used_at,revoked_at"), {"u":user_id,"n":name.strip(),"p":_prefix(raw),"h":_hash(raw),"e":expires_at}).mappings().one()
        return dict(row),raw
def list_api_tokens(user_id: int) -> list[dict]:
    with get_connection() as c: return [dict(x) for x in c.execute(text("SELECT id,name,token_prefix,created_at,expires_at,last_used_at,revoked_at FROM api_tokens WHERE user_id=:u AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>:n) ORDER BY created_at DESC"), {"u":user_id,"n":_now()}).mappings()]
def revoke_api_token(user_id: int, token_id: int) -> bool:
    with get_connection() as c: return c.execute(text("UPDATE api_tokens SET revoked_at=:n WHERE id=:i AND user_id=:u AND revoked_at IS NULL"), {"n":_now(),"i":token_id,"u":user_id}).rowcount > 0
def authenticate_api_token(raw_token: str) -> int | None: return _authenticate(raw_token, "api_tokens")
