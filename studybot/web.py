"""Web app served off the MCP server's existing Starlette/uvicorn instance.

Mounted as custom routes rather than a second service: nginx already terminates
TLS and proxies everything on this host to port 8811, so this costs no new
systemd unit, port, firewall rule, or dependency.
"""
from datetime import datetime, timedelta, timezone

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from studybot import db
from studybot.review import apply_review, card_payload

INDEX = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0b0d10">
<link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#128218;</text></svg>">
<title>Study</title>
<style>
:root{--bg:#0b0d10;--fg:#e8eaed;--dim:#9aa0a6;--card:#16191d;--line:#282c31;--accent:#4a9eff}
@media(prefers-color-scheme:light){:root{--bg:#f7f8fa;--fg:#1a1c1e;--dim:#5f6368;--card:#fff;--line:#e0e3e7}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}
header{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line)}
header b{font-size:17px}
#count{color:var(--dim);font-size:14px}
main{padding:16px;max-width:680px;margin:0 auto;padding-bottom:80px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:14px}
.q{font-size:20px;font-weight:600;white-space:pre-wrap}
.a{margin-top:16px;padding-top:16px;border-top:1px solid var(--line);white-space:pre-wrap}
.notes{margin-top:12px;color:var(--dim);font-size:14px;white-space:pre-wrap}
.tags{margin-top:12px;color:var(--dim);font-size:13px}
button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);
 border-radius:12px;padding:14px;cursor:pointer;min-height:48px}
button:active{opacity:.6}
.wide{width:100%;font-weight:600;background:var(--accent);color:#fff;border:0}
.rate{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.rate button{font-size:13px;padding:12px 4px}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:var(--card);
 border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom)}
nav button{flex:1;border:0;border-radius:0;background:none;color:var(--dim);font-size:13px;padding:12px 0}
nav button.on{color:var(--accent);font-weight:600}
input{width:100%;font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);
 border-radius:12px;padding:12px;margin-bottom:14px}
.row{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:8px;cursor:pointer}
.row .qq{font-weight:500}
.row .aa{margin-top:8px;color:var(--dim);font-size:14px;white-space:pre-wrap}
.empty{text-align:center;color:var(--dim);padding:48px 16px}
.kbd{color:var(--dim);font-size:12px;text-align:center;margin-top:14px}
.tabs{display:flex;gap:8px;margin-bottom:14px}
.tabs button{flex:1}
.tabs button.on{background:var(--accent);color:#fff}
@media(hover:none){.kbd{display:none}}
</style></head><body>
<div id="root"></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function getToken(){ return localStorage.getItem('session')||'' }
function setToken(t){ t?localStorage.setItem('session',t):localStorage.removeItem('session') }
async function api(p,o={}){
  const headers={...(o.headers||{}), Authorization:'Bearer '+getToken()};
  const res=await fetch(p,{...o,headers});
  if(res.status===401){ setToken(null); renderAuth(); throw new Error('unauthorized'); }
  return res.json();
}

function renderAuth(){
  let mode='login';
  const root=$('#root');
  function draw(){
    root.innerHTML=`<main style="padding-top:80px">
      <div class="card" style="max-width:320px;margin:0 auto">
        <div class="q" style="margin-bottom:16px">${mode==='register'?'Create account':'Log in'}</div>
        <div class="tabs">
          <button class="${mode==='login'?'on':''}" id="t-login">Log in</button>
          <button class="${mode==='register'?'on':''}" id="t-register">Register</button>
        </div>
        <input id="u" placeholder="Username" autocomplete="username">
        <input id="p" placeholder="Password" type="password" autocomplete="current-password">
        <div id="err" class="notes" style="color:#e05555"></div>
        <button class="wide" id="go">${mode==='register'?'Create account':'Log in'}</button>
      </div>
    </main>`;
    $('#t-login').onclick=()=>{mode='login';draw()};
    $('#t-register').onclick=()=>{mode='register';draw()};
    $('#go').onclick=async()=>{
      const username=$('#u').value.trim(), password=$('#p').value;
      const path=mode==='register'?'/api/auth/register':'/api/auth/login';
      const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username,password})});
      const data=await res.json();
      if(!res.ok){ $('#err').textContent=data.error||'Failed'; return; }
      setToken(data.token); renderApp();
    };
  }
  draw();
}

function renderApp(){
  const root=$('#root');
  root.innerHTML=`<header><b id="title">Review</b><span style="display:flex;align-items:center;gap:10px">
    <span id="count"></span><button id="logout" style="padding:4px 10px;min-height:auto;font-size:12px">Log out</button></span></header>
  <main id="view"></main>
  <nav>
    <button id="tab-review" class="on">Review</button>
    <button id="tab-browse">Browse</button>
    <button id="tab-stats">Stats</button>
    <button id="tab-notes">Notes</button>
    <button id="tab-tokens">Tokens</button>
  </nav>`;
  const view=$('#view');
  let tab='review', queue=[], idx=0, shown=false;

  $('#logout').onclick=async()=>{
    await api('/api/auth/logout',{method:'POST'}).catch(()=>{});
    setToken(null); renderAuth();
  };

  function go(t){
    tab=t;
    for(const k of ['review','browse','stats','notes','tokens']) $('#tab-'+k).classList.toggle('on',k===t);
    $('#title').textContent={review:'Review',browse:'Browse',stats:'Stats',notes:'Notes',tokens:'Tokens'}[t];
    ({review:loadReview,browse:loadBrowse,stats:loadStats,notes:loadNotes,tokens:loadTokens}[t])();
  }
  for(const k of ['review','browse','stats','notes','tokens']) $('#tab-'+k).onclick=()=>go(k);

  async function loadReview(){
    view.innerHTML='<div class="empty">Loading…</div>';
    queue=await api('/api/due'); idx=0; shown=false; renderCard();
  }

  function renderCard(){
    const c=queue[idx];
    $('#count').textContent=queue.length?`${idx+1}/${queue.length}`:'';
    if(!c){ view.innerHTML='<div class="empty">Nothing due. <br><br>🎉</div>'; return; }
    view.innerHTML=`<div class="card">
      <div class="q">${esc(c.front)}</div>
      ${shown?`<div class="a">${esc(c.back)}</div>`:''}
      ${shown&&c.notes?`<div class="notes">📝 ${esc(c.notes)}</div>`:''}
      ${c.tags?`<div class="tags">🏷 ${esc(c.tags)}</div>`:''}
    </div>
    ${shown?`<div class="rate">
      <button onclick="window.__rate(1)">🔴 Again</button>
      <button onclick="window.__rate(3)">🟠 Hard</button>
      <button onclick="window.__rate(4)">🟢 Good</button>
      <button onclick="window.__rate(5)">🔵 Easy</button></div>
      <div class="kbd">1–4 to rate</div>`
     :`<button class="wide" onclick="window.__reveal()">Reveal</button>
       <div class="kbd">space to reveal</div>`}`;
  }
  window.__reveal=()=>{ shown=true; renderCard(); };
  window.__rate=async(q)=>{
    const c=queue[idx]; if(!c) return;
    shown=false;
    const r=await api('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:c.id,quality:q})});
    if(q===1) queue.push(c);
    idx++; renderCard();
    if(r&&r.interval_days) $('#count').textContent=`${Math.min(idx+1,queue.length)}/${queue.length} · +${r.interval_days}d`;
  };

  async function loadBrowse(){
    $('#count').textContent='';
    view.innerHTML='<input id="q" placeholder="Search cards…" autocomplete="off"><div id="list"></div>';
    const q=$('#q');
    let t; q.oninput=()=>{clearTimeout(t);t=setTimeout(()=>search(q.value),200)};
    q.focus(); search('');
  }
  async function search(term){
    const cards=await api('/api/cards?q='+encodeURIComponent(term));
    $('#list').innerHTML=cards.length?cards.map(c=>
      `<div class="row" onclick="this.querySelector('.aa').hidden=!this.querySelector('.aa').hidden">
         <div class="qq">${esc(c.front)}</div>
         <div class="aa" hidden>${esc(c.back)}${c.tags?'<br>🏷 '+esc(c.tags):''}</div>
       </div>`).join(''):'<div class="empty">No cards.</div>';
  }

  async function loadStats(){
    $('#count').textContent='';
    const s=await api('/api/stats');
    const weak=Object.entries(s.retention.by_tag).slice(0,8);
    view.innerHTML=`<div class="card">
      <div class="q">${s.retention.retention}% retention</div>
      <div class="notes">${s.retention.reviews} reviews in the last 30 days</div></div>
     <div class="card"><div class="q">${s.deck.total} cards</div>
      <div class="notes">${s.deck.due} due · ${s.deck.suspended} suspended · 🔥 ${s.streak} day streak</div></div>
     ${weak.length?`<div class="card"><div class="q" style="font-size:16px">Weakest tags</div>${
       weak.map(([t,d])=>`<div class="notes">${esc(t)} — ${d.retention}% (${d.reviews})</div>`).join('')}</div>`:''}
     <div class="card"><div class="q" style="font-size:16px">Next 7 days</div>${
       s.forecast.map(f=>`<div class="notes">${esc(f.day)} — ${f.due}</div>`).join('')}</div>`;
  }

  async function loadNotes(){
    $('#count').textContent='';
    const notes=await api('/api/notes');
    view.innerHTML=notes.length?notes.map(n=>
      `<div class="card">
         <div class="q" style="font-size:16px">${esc(n.topic)}</div>
         <div class="notes">${esc(n.content)}</div>
         ${n.tags?`<div class="tags">🏷 ${esc(n.tags)}</div>`:''}
       </div>`).join(''):'<div class="empty">No session notes yet.</div>';
  }

  async function loadTokens(){
    $('#count').textContent='';
    const tokens=await api('/api/tokens');
    view.innerHTML=`<button class="wide" id="new-token" style="margin-bottom:14px">+ New MCP token</button>
      <div id="created"></div>` +
      (tokens.length?tokens.map(t=>
        `<div class="row"><div class="qq">${esc(t.name)}</div>
         <div class="aa" style="display:block">Created ${t.created_at.slice(0,10)}${t.expires_at?' · expires '+t.expires_at.slice(0,10):''}${t.last_used_at?' · last used '+t.last_used_at.slice(0,10):' · never used'}</div>
         <button data-id="${t.id}" class="revoke" style="margin-top:8px;font-size:12px;padding:6px 10px;min-height:auto">Revoke</button>
        </div>`).join(''):'<div class="empty">No MCP tokens yet. Create one to connect Claude or another AI client.</div>');
    $('#new-token').onclick=async()=>{
      const name=prompt('Token name (e.g. "Claude Desktop"):'); if(!name) return;
      const r=await api('/api/tokens',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
      $('#created').innerHTML=`<div class="card"><div class="q" style="font-size:15px">Copy this now — shown once</div>
        <div class="notes" style="word-break:break-all;font-family:monospace">${esc(r.token)}</div></div>`;
      loadTokens();
    };
    document.querySelectorAll('.revoke').forEach(b=>b.onclick=async()=>{
      await api('/api/tokens/'+b.dataset.id,{method:'DELETE'}); loadTokens();
    });
  }

  document.onkeydown=e=>{
    if(tab!=='review') return;
    if(e.key===' '&&!shown){e.preventDefault();window.__reveal();}
    else if(shown&&['1','2','3','4'].includes(e.key)) window.__rate([1,3,4,5][+e.key-1]);
  };
  go('review');
}

getToken()?renderApp():renderAuth();
</script></body></html>"""


def _iso(dt):
    return dt.isoformat() if dt else None


def register(server) -> None:
    """Attach the web app to an MCPServer's Starlette app."""

    def user_id(request: Request):
        """Set by _RequireAuth in mcp/server.py before this handler ever runs."""
        return getattr(getattr(request, "state", None), "study_user_id", None)

    @server.custom_route("/app", methods=["GET"])
    async def index(request: Request) -> Response:
        return HTMLResponse(INDEX)

    @server.custom_route("/api/due", methods=["GET"])
    async def due(request: Request) -> Response:
        return JSONResponse([card_payload(c) for c in db.list_due_cards(user_id(request))])

    @server.custom_route("/api/cards", methods=["GET"])
    async def cards(request: Request) -> Response:
        uid = user_id(request)
        term = request.query_params.get("q", "").strip()
        rows = db.search_cards(uid, term) if term else db.list_all_cards(uid)
        return JSONResponse([card_payload(c) for c in rows[:200]])

    @server.custom_route("/api/answer", methods=["POST"])
    async def answer(request: Request) -> Response:
        body = await request.json()
        try:
            card_id, quality = int(body["id"]), int(body["quality"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "id and quality required"}, status_code=400)
        if quality not in (1, 3, 4, 5):
            return JSONResponse({"error": "quality must be 1, 3, 4 or 5"}, status_code=400)
        card = apply_review(user_id(request), card_id, quality)
        if card is None:
            return JSONResponse({"error": "card not found"}, status_code=404)
        return JSONResponse({
            "id": card["id"], "interval_days": card["interval_days"], "due_at": _iso(card["due_at"]),
        })

    @server.custom_route("/api/stats", methods=["GET"])
    async def stats(request: Request) -> Response:
        uid = user_id(request)
        return JSONResponse({
            "deck": db.get_stats(uid),
            "retention": db.get_retention_stats(uid, days=30),
            "streak": db.get_streak_info(uid)["current"],
            "forecast": [{"day": d, "due": n} for d, n in db.get_forecast(uid, days=7)],
        })

    @server.custom_route("/api/notes", methods=["GET"])
    async def notes(request: Request) -> Response:
        rows = db.list_session_notes(user_id(request))
        for r in rows:
            r["created_at"] = _iso(r["created_at"])
        return JSONResponse(rows)

    @server.custom_route("/api/notes", methods=["POST"])
    async def add_note(request: Request) -> Response:
        body = await request.json()
        topic = (body.get("topic") or "").strip()
        content = (body.get("content") or "").strip()
        if not topic or not content:
            return JSONResponse({"error": "topic and content required"}, status_code=400)
        tags = (body.get("tags") or "").strip() or None
        note_id = db.add_session_note(user_id(request), topic, content, tags=tags)
        return JSONResponse({"id": note_id, "topic": topic})

    # --- MCP personal access tokens ---

    @server.custom_route("/api/tokens", methods=["GET"])
    async def list_tokens(request: Request) -> Response:
        rows = db.list_api_tokens(user_id(request))
        for r in rows:
            for key in ("created_at", "expires_at", "last_used_at", "revoked_at"):
                r[key] = _iso(r.get(key))
        return JSONResponse(rows)

    @server.custom_route("/api/tokens", methods=["POST"])
    async def create_token(request: Request) -> Response:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)
        expires_at = None
        if body.get("expires_days"):
            try:
                expires_at = datetime.now(timezone.utc) + timedelta(days=int(body["expires_days"]))
            except (TypeError, ValueError):
                return JSONResponse({"error": "expires_days must be a number"}, status_code=400)
        try:
            info, raw = db.create_api_token(user_id(request), name, expires_at=expires_at)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        for key in ("created_at", "expires_at", "last_used_at", "revoked_at"):
            info[key] = _iso(info.get(key))
        return JSONResponse({**info, "token": raw}, status_code=201)

    @server.custom_route("/api/tokens/{token_id}", methods=["DELETE"])
    async def revoke_token(request: Request) -> Response:
        try:
            token_id = int(request.path_params["token_id"])
        except (KeyError, ValueError):
            return JSONResponse({"error": "invalid token id"}, status_code=400)
        if not db.revoke_api_token(user_id(request), token_id):
            return JSONResponse({"error": "token not found"}, status_code=404)
        return JSONResponse({"ok": True})

    # --- auth: these stay reachable without a session (see _RequireAuth in
    # mcp/server.py, which special-cases /api/auth/*). Registration is open —
    # any number of accounts, no single-account gate. ---

    @server.custom_route("/api/auth/register", methods=["POST"])
    async def auth_register(request: Request) -> Response:
        body = await request.json()
        try:
            _, token = db.register_user(
                body.get("username", ""), body.get("password", ""), body.get("display_name"),
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"token": token}, status_code=201)

    @server.custom_route("/api/auth/login", methods=["POST"])
    async def auth_login(request: Request) -> Response:
        body = await request.json()
        token = db.login(body.get("username", ""), body.get("password", ""))
        if token is None:
            return JSONResponse({"error": "invalid username or password"}, status_code=401)
        return JSONResponse({"token": token})

    @server.custom_route("/api/auth/logout", methods=["POST"])
    async def auth_logout(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        db.logout(token)
        return JSONResponse({"ok": True})

    @server.custom_route("/api/auth/change-password", methods=["POST"])
    async def auth_change_password(request: Request) -> Response:
        body = await request.json()
        try:
            ok = db.change_password(user_id(request), body.get("old_password", ""), body.get("new_password", ""))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if not ok:
            return JSONResponse({"error": "old password is incorrect"}, status_code=401)
        return JSONResponse({"ok": True})
