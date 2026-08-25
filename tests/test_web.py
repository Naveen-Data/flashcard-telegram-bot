"""Self-check for the web API. Run: python tests/test_web.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import studybot.db.connection as connection  # noqa: E402
connection.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"

from starlette.testclient import TestClient  # noqa: E402

from studybot import db  # noqa: E402
from studybot.mcp.server import mcp_server  # noqa: E402
from studybot.review import card_payload  # noqa: E402

db.init_db()
CHAT = 7
db.set_registered_chat_id(CHAT)

basic = db.add_card("What is X?", "X is Y", CHAT, tags="t", notes="because")
cloze = db.add_card("The {{c1::mitochondria}} is the powerhouse", "", CHAT, card_type="cloze")

p = card_payload(db.get_card(cloze))
assert p["front"] == "The [...] is the powerhouse", p["front"]
assert p["back"] == "The mitochondria is the powerhouse", p["back"]

client = TestClient(mcp_server.streamable_http_app(streamable_http_path="/mcp"))

assert "<title>Study</title>" in client.get("/app").text

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

print("web api ok")
