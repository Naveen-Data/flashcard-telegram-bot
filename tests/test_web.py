"""Self-check for the web API. Run: python tests/test_web.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import studybot.db.connection as connection  # noqa: E402
connection.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"

from starlette.testclient import TestClient  # noqa: E402

from studybot import db  # noqa: E402
from studybot.mcp.server import build_app  # noqa: E402
from studybot.review import card_payload  # noqa: E402

db.init_db()
CHAT = 7
db.set_registered_chat_id(CHAT)

basic = db.add_card("What is X?", "X is Y", CHAT, tags="t", notes="because")
cloze = db.add_card("The {{c1::mitochondria}} is the powerhouse", "", CHAT, card_type="cloze")

p = card_payload(db.get_card(cloze))
assert p["front"] == "The [...] is the powerhouse", p["front"]
assert p["back"] == "The mitochondria is the powerhouse", p["back"]

anon = TestClient(build_app())

# --- auth: register, login, wrong password, one-account-only, sessions ---

assert anon.get("/api/auth/status").json() == {"registered": False}
assert anon.get("/api/due").status_code == 401, "no session yet — must be rejected"

r = anon.post("/api/auth/register", json={"username": "testuser", "password": "short"})
assert r.status_code == 409, "under 8 chars must be rejected"

r = anon.post("/api/auth/register", json={"username": "testuser", "password": "test-fixture-pw-1"})
assert r.status_code == 201, r.text
token = r.json()["token"]
assert token

assert anon.get("/api/auth/status").json() == {"registered": True}

r = anon.post("/api/auth/register", json={"username": "someoneelse", "password": "whatever12"})
assert r.status_code == 409, "a second account must be refused"

assert anon.post("/api/auth/login", json={"username": "testuser", "password": "wrong"}).status_code == 401
r = anon.post("/api/auth/login", json={"username": "testuser", "password": "test-fixture-pw-1"})
assert r.status_code == 200 and r.json()["token"], "correct password must log in"

client = TestClient(build_app(), headers={"Authorization": f"Bearer {token}"})
assert client.get("/api/due").status_code == 200, "valid session must be accepted"
assert anon.get("/api/due", headers={"Authorization": "Bearer garbage"}).status_code == 401

# --- everything below uses the authenticated client ---

due = client.get("/api/due").json()
assert due == [], f"nothing is due one day after creation, got {due}"

# Make both due, then check they surface with cloze rendered.
with connection.get_connection() as conn:
    conn.execute("UPDATE cards SET due_at='2000-01-01T00:00:00'")
    conn.commit()
due = client.get("/api/due").json()
assert len(due) == 2, due
assert all("front" in c and "back" in c for c in due)

before = db.get_card(basic)["due_at"]
r = client.post("/api/answer", json={"id": basic, "quality": 4})
assert r.status_code == 200, r.text
assert r.json()["interval_days"] >= 1
assert db.get_card(basic)["due_at"] != before, "answering must reschedule"
assert db.get_card(basic)["stability"] is not None, "FSRS state must be written"
assert db.apply_undo(CHAT) is True, "web answers must be undoable from Telegram"

assert client.post("/api/answer", json={"id": basic, "quality": 99}).status_code == 400
assert client.post("/api/answer", json={"id": 999999, "quality": 4}).status_code == 404
assert client.post("/api/answer", json={"quality": 4}).status_code == 400

assert len(client.get("/api/cards").json()) == 2
assert len(client.get("/api/cards?q=mitochondria").json()) == 1
assert client.get("/api/cards?q=zzzznope").json() == []

s = client.get("/api/stats").json()
assert s["deck"]["total"] == 2
assert len(s["forecast"]) == 8

# logout must actually invalidate the session
assert client.post("/api/auth/logout").status_code == 200
assert client.get("/api/due").status_code == 401, "session must be dead after logout"

# --- public surfaces: unaffected by any of the above ---

assert "<title>Study</title>" in anon.get("/app").text, "SPA shell never requires a session"

r = anon.get("/api/due", headers={"Origin": "http://localhost:5173"})
assert r.status_code == 401  # still gated — this only checks the CORS header shape
assert r.headers.get("access-control-allow-origin") == "http://localhost:5173", dict(r.headers)
r = anon.get("/api/due", headers={"Origin": "https://evil.example.com"})
assert "access-control-allow-origin" not in r.headers, "must not reflect arbitrary origins"

r = anon.options(
    "/api/due",
    headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
)
assert r.status_code < 400, "CORS preflight must never be gated"

# --- /mcp: separate static-secret model for Claude Code, independent of sessions ---

with TestClient(build_app()) as mcp_anon:
    assert mcp_anon.post("/mcp").status_code != 401, "no-op when MCP_AUTH_TOKEN is unset"

os.environ["MCP_AUTH_TOKEN"] = "mcp-secret"
with TestClient(build_app()) as mcp_gated:
    assert mcp_gated.post("/mcp").status_code == 401
    assert mcp_gated.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert mcp_gated.post("/mcp", headers={"Authorization": "Bearer mcp-secret"}).status_code != 401
    assert mcp_gated.get("/api/auth/status").status_code == 200, "/mcp token must not affect /api/auth/*"
del os.environ["MCP_AUTH_TOKEN"]

print("web api ok")
