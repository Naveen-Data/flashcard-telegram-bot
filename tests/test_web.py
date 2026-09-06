"""Self-check for the multi-user Postgres stack. Run:

    PGHOST=localhost PGUSER=user PGPASSWORD=pw PGDATABASE=studybot_test \\
        python tests/test_web.py

Needs a real Postgres database with the schema already applied (`alembic upgrade
head`). Safe to re-run: every user/token is created under a fresh random suffix.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not os.environ.get("PGHOST"):
    sys.exit(
        "Set PGHOST/PGUSER/PGPASSWORD/PGDATABASE for a Postgres database with the "
        "schema applied (alembic upgrade head)."
    )

from sqlalchemy import text  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from studybot import db  # noqa: E402
from studybot.bot import callbacks, commands  # noqa: E402
from studybot.db.connection import get_connection  # noqa: E402
from studybot.mcp.server import _RATE_LIMIT_RULES, build_app  # noqa: E402
from studybot.review import card_payload  # noqa: E402

failures = []


def check(label, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}")
    if not cond:
        failures.append(label)


suffix = str(int(time.time() * 1000))

# --- db layer: users, isolation, cloze rendering -----------------------------

uid1, sess1 = db.register_user(f"alice{suffix}", "correct-horse-1")
uid2, sess2 = db.register_user(f"bob{suffix}", "correct-horse-2")
check("two users get distinct ids", uid1 != uid2)
check("wrong password rejected", db.login(f"alice{suffix}", "wrong") is None)
try:
    db.register_user(f"alice{suffix}", "whatever12")
    check("duplicate username rejected", False)
except ValueError:
    check("duplicate username rejected", True)

cloze = db.add_card("The {{c1::mitochondria}} is the powerhouse", "", uid1, card_type="cloze")
p = card_payload(db.get_card(uid1, cloze))
check("cloze front masks the answer", p["front"] == "The [...] is the powerhouse", p["front"])
check("cloze back reveals it", p["back"] == "The mitochondria is the powerhouse", p["back"])

basic = db.add_card("What is X?", "X is Y", uid1, tags="t", notes="because")
other = db.add_card("Bob's card", "answer", uid2)
check("bob cannot read alice's card", db.get_card(uid2, basic) is None)
check("bob cannot edit alice's card", db.edit_card(uid2, basic, question="hacked") is False)
check("alice's card unaffected", db.get_card(uid1, basic)["question"] == "What is X?")

# --- telegram linking ---------------------------------------------------------

CHAT1 = 900000 + int(suffix) % 100000
tg_user_id = db.get_or_create_telegram_user(CHAT1, CHAT1, "alice_tg", "Alice")
check("telegram identity resolves to a user", db.get_user_id_for_telegram(CHAT1, CHAT1) == tg_user_id)
targets = db.list_notification_targets()
check("notification targets include the linked chat", any(t["user_id"] == tg_user_id for t in targets))

# --- api tokens: create/list/revoke/isolation ---------------------------------

info, raw1 = db.create_api_token(uid1, "Claude Desktop")
check("token metadata excludes the hash", "token_hash" not in info)
check("valid token authenticates as its owner", db.authenticate_api_token(raw1) == uid1)
check("garbage token fails", db.authenticate_api_token(raw1 + "x") is None)
check("revoking someone else's token id fails", db.revoke_api_token(uid2, info["id"]) is False)
check("owner can revoke their own token", db.revoke_api_token(uid1, info["id"]) is True)
check("revoked token no longer authenticates", db.authenticate_api_token(raw1) is None)

_, raw1 = db.create_api_token(uid1, "Second token")
_, raw2 = db.create_api_token(uid2, "Bob's token")

# --- web API: auth, cards, tokens, isolation ---------------------------------

anon = TestClient(build_app(host="0.0.0.0"), base_url="http://127.0.0.1")

r = anon.post("/api/auth/register", json={"username": f"carol{suffix}", "password": "short"})
check("password under 8 chars rejected", r.status_code == 400, r.text)

r = anon.post("/api/auth/register", json={"username": f"carol{suffix}", "password": "test-fixture-pw-1"})
check("open registration works, no single-account gate", r.status_code == 201, r.text)

assert anon.get("/api/due").status_code == 401
r = anon.post("/api/auth/login", json={"username": f"alice{suffix}", "password": "wrong"})
check("wrong password on web login rejected", r.status_code == 401)
r = anon.post("/api/auth/login", json={"username": f"alice{suffix}", "password": "correct-horse-1"})
check("correct password logs in", r.status_code == 200 and r.json()["token"])
web_sess1 = r.json()["token"]

client1 = TestClient(build_app(host="0.0.0.0"), base_url="http://127.0.0.1",
                      headers={"Authorization": f"Bearer {web_sess1}"})
check("valid session accepted", client1.get("/api/due").status_code == 200)
check("garbage session rejected", anon.get("/api/due", headers={"Authorization": "Bearer garbage"}).status_code == 401)

with get_connection() as conn:
    conn.execute(text("UPDATE cards SET due_at = now() - interval '1 day' WHERE user_id=:u"), {"u": uid1})
due = client1.get("/api/due").json()
check("due list only has alice's cards, cloze rendered", len(due) == 2 and all("front" in c for c in due))

r = client1.post("/api/answer", json={"id": basic, "quality": 4})
check("answer succeeds and serializes due_at as a string", r.status_code == 200 and isinstance(r.json()["due_at"], str))
check("answering wrote FSRS state", db.get_card(uid1, basic)["stability"] is not None)
check("web answers are undoable", db.apply_undo(uid1) is True)

check("answer with bad quality rejected", client1.post("/api/answer", json={"id": basic, "quality": 99}).status_code == 400)
check("answer on nonexistent card is 404", client1.post("/api/answer", json={"id": 999999999, "quality": 4}).status_code == 404)

# edit/delete a card via the web API
r = client1.patch(f"/api/cards/{basic}", json={"question": "What is X really?", "tags": "edited"})
check("edit card via web", r.status_code == 200 and r.json()["question"] == "What is X really?", r.text[:200])
check("edit card is owner-scoped", client1.patch(f"/api/cards/{other}", json={"question": "hacked"}).status_code == 404)
check("edit nonexistent card is 404", client1.patch("/api/cards/999999999", json={"question": "x"}).status_code == 404)
check("delete someone else's card is 404", client1.delete(f"/api/cards/{other}").status_code == 404)
scratch_card = db.add_card("scratch", "scratch", uid1)
check("delete own card via web", client1.delete(f"/api/cards/{scratch_card}").status_code == 200)
check("card actually gone", db.get_card(uid1, scratch_card) is None)

r = client1.post("/api/notes", json={"topic": "t", "content": "c"})
check("add note", r.status_code == 200)
r = client1.get("/api/notes")
check("notes serialize created_at as a string", isinstance(r.json()[0]["created_at"], str))
note_id = r.json()[0]["id"]

check("bob cannot edit alice's note", db.edit_session_note(uid2, note_id, content="hacked") is False)
check("bob cannot delete alice's note", db.delete_session_note(uid2, note_id) is False)

# edit/delete a note via the web API
r = client1.patch(f"/api/notes/{note_id}", json={"content": "updated content"})
check("edit note via web", r.status_code == 200, r.text[:200])
check("note actually updated", any(n["content"] == "updated content" for n in db.list_session_notes(uid1)))
check("edit nonexistent note is 404", client1.patch("/api/notes/999999999", json={"content": "x"}).status_code == 404)
check("delete note via web", client1.delete(f"/api/notes/{note_id}").status_code == 200)
check("note actually gone", not any(n["id"] == note_id for n in db.list_session_notes(uid1)))

# change-password: requires a live session, old password must be correct
r = client1.post("/api/auth/change-password", json={"old_password": "wrong", "new_password": "new-pw-12345"})
check("change-password rejects wrong old password", r.status_code == 401)
r = client1.post("/api/auth/change-password", json={"old_password": "correct-horse-1", "new_password": "new-pw-12345"})
check("change-password succeeds with correct old password", r.status_code == 200, r.text)
check("old password no longer logs in", db.login(f"alice{suffix}", "correct-horse-1") is None)
check("new password logs in", db.login(f"alice{suffix}", "new-pw-12345") is not None)

# tokens via the web API
r = client1.get("/api/tokens")
check("list tokens", r.status_code == 200 and len(r.json()) == 1)
r = client1.post("/api/tokens", json={"name": "Third client"})
check("create token via web, raw value returned once", r.status_code == 201 and "token" in r.json())
check("token listing never exposes a hash", "token_hash" not in r.json())
new_token_id = r.json()["id"]
client2 = TestClient(build_app(host="0.0.0.0"), base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {sess2}"})
check("cannot revoke someone else's token", client2.delete(f"/api/tokens/{new_token_id}").status_code == 404)
check("owner can revoke their token", client1.delete(f"/api/tokens/{new_token_id}").status_code == 200)

# logout invalidates the session
check("logout succeeds", client1.post("/api/auth/logout").status_code == 200)
check("session dead after logout", client1.get("/api/due").status_code == 401)

# public surfaces
check("SPA shell never requires a session", "Study" in anon.get("/app").text)
r = anon.get("/api/due", headers={"Origin": "http://localhost:5173"})
check("CORS header present for allowed dev origin", r.headers.get("access-control-allow-origin") == "http://localhost:5173")
r = anon.get("/api/due", headers={"Origin": "https://evil.example.com"})
check("CORS does not reflect arbitrary origins", "access-control-allow-origin" not in r.headers)

# every method an actual browser will preflight for must be allowed, or the
# real request never leaves the browser (TestClient itself doesn't enforce
# preflight, so this has to be checked explicitly, not just exercised)
r = anon.options("/api/tokens/1", headers={"Origin": "http://localhost:5173",
                                            "Access-Control-Request-Method": "DELETE"})
check("CORS preflight allows DELETE (token revoke)", r.status_code < 400, r.text[:200])

r = anon.options("/api/cards/1", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PATCH"})
check("CORS preflight allows PATCH (card/note edit)", r.status_code < 400, r.text[:200])

# claude.ai must be allowed to preflight /mcp when it's in WEB_ALLOWED_ORIGINS
# (production always sets it there) — otherwise Claude's own client is blocked
# by the browser before a bearer token is ever checked, which surfaces to the
# user as "can't connect to a valid MCP server"
os.environ["WEB_ALLOWED_ORIGINS"] = "https://claude.ai"
with TestClient(build_app(host="0.0.0.0")) as claude_origin_client:
    r = claude_origin_client.options("/mcp", headers={"Origin": "https://claude.ai", "Access-Control-Request-Method": "POST",
                                                        "Access-Control-Request-Headers": "authorization,content-type"})
    check("CORS preflight allows claude.ai to reach /mcp when configured",
          r.status_code < 400 and r.headers.get("access-control-allow-origin") == "https://claude.ai", r.text[:200])
del os.environ["WEB_ALLOWED_ORIGINS"]

# --- /mcp: real per-user bearer tokens, no static shared secret --------------

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def mcp_session(client, token):
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
                     json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "1"}}})
    if r.status_code != 200:
        return None, r
    sid = r.headers.get("Mcp-Session-Id")
    client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}", "Mcp-Session-Id": sid},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return sid, r


def mcp_call(client, token, sid, name, args):
    return client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}", "Mcp-Session-Id": sid},
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}})


def tool_result(r):
    msg = None
    for line in r.text.splitlines():
        if line.startswith("data: "):
            msg = json.loads(line[6:])
    msg = msg or r.json()
    result = msg.get("result", {})
    if result.get("structuredContent") is not None:
        sc = result["structuredContent"]
        return sc.get("result", sc)
    content = result.get("content", [])
    if content and content[0].get("text"):
        return json.loads(content[0]["text"])
    return result


with TestClient(build_app(host="0.0.0.0"), base_url="http://127.0.0.1") as mcp_client:
    sid1, r = mcp_session(mcp_client, raw1)
    check("mcp session initializes with a valid personal token", sid1 is not None, r.text[:200])

    r = mcp_call(mcp_client, raw1, sid1, "add_card", {"question": "mcp Q", "answer": "mcp A"})
    added = tool_result(r)
    mcp_card_id = added.get("id")
    check("mcp add_card resolves to the token's owner", mcp_card_id is not None, str(added))

    sid2, _ = mcp_session(mcp_client, raw2)
    r = mcp_call(mcp_client, raw2, sid2, "get_card_history", {"card_id": mcp_card_id})
    check("a different user's token cannot read this card via mcp", "error" in tool_result(r))

    check("mcp with no token is unauthorized",
          mcp_client.post("/mcp", headers=MCP_HEADERS,
                           json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                            "clientInfo": {"name": "x", "version": "1"}}}).status_code == 401)
    check("mcp with garbage token is unauthorized",
          mcp_client.post("/mcp", headers={**MCP_HEADERS, "Authorization": "Bearer garbage-token"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                            "clientInfo": {"name": "x", "version": "1"}}}).status_code == 401)

# --- rate limiting: unaffected by the auth model change ----------------------


def _limit_for(prefix, method):
    for p, m, limit, _window in _RATE_LIMIT_RULES:
        if p == prefix and m == method:
            return limit
    raise AssertionError(f"no rate limit rule for {method} {prefix}")


login_limit = _limit_for("/api/auth/login", "POST")
with TestClient(build_app(host="0.0.0.0")) as limited:
    for _ in range(login_limit):
        limited.post("/api/auth/login", json={"username": "nope", "password": "nope"})
    r = limited.post("/api/auth/login", json={"username": "nope", "password": "nope"})
    check("attempt past the login limit gets rate limited", r.status_code == 429)
    check("Retry-After header present", "Retry-After" in r.headers)
    r = limited.post("/api/auth/login", json={"username": "nope", "password": "nope"},
                      headers={"X-Forwarded-For": "203.0.113.9"})
    check("a different client IP gets its own bucket", r.status_code == 401)

# --- bot handlers: real Postgres, mocked Telegram Update objects -------------


def fake_update(tg_user_id, chat_id, text=None):
    u = MagicMock()
    u.effective_user.id, u.effective_user.username, u.effective_user.full_name = tg_user_id, f"tg{tg_user_id}", f"TG {tg_user_id}"
    u.effective_chat.id = chat_id
    u.message = AsyncMock()
    u.message.text = text or ""
    u.message.reply_text = AsyncMock()
    return u


def fake_context():
    ctx = MagicMock()
    ctx.args, ctx.chat_data, ctx.bot_data, ctx.bot = [], {}, {}, AsyncMock()
    return ctx


async def _run_bot_checks():
    TG = 800000 + int(suffix) % 100000
    u = fake_update(TG, TG)
    await commands.start_command(u, fake_context())
    bot_uid = db.get_user_id_for_telegram(TG, TG)
    check("bot /start creates a resolvable user", bot_uid is not None)

    unreg = fake_update(TG + 1, TG + 1)
    await commands.tags_command(unreg, fake_context())
    check("commands before /start ask for /start, not a crash",
          "start" in unreg.message.reply_text.call_args[0][0].lower())

    add_upd = fake_update(TG, TG, text="add: bot Q? / bot A #tag")
    await commands.handle_add_card(add_upd, fake_context())
    bot_cards = db.list_all_cards(bot_uid)
    check("bot add: creates a card owned by the telegram user", len(bot_cards) == 1)

    with get_connection() as conn:
        conn.execute(text("UPDATE cards SET due_at = now() - interval '1 hour' WHERE id=:id"),
                     {"id": bot_cards[0]["id"]})
    ctx = fake_context()
    await commands.review_command(fake_update(TG, TG), ctx)
    check("review_command queues the due card", ctx.chat_data.get("review_queue") == [bot_cards[0]["id"]])

    cb = MagicMock()
    cb.effective_user.id = TG
    cb.callback_query = AsyncMock()
    cb.callback_query.data = f"ans:4:{bot_cards[0]['id']}"
    cb.callback_query.message.chat_id = TG
    cb.callback_query.message.photo = None
    await callbacks.handle_callback(cb, ctx)
    check("callback answer updates FSRS state", db.get_card(bot_uid, bot_cards[0]["id"])["stability"] is not None)


asyncio.run(_run_bot_checks())

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("web api ok")
