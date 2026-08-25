"""Web app served off the MCP server's existing Starlette/uvicorn instance.

Mounted as custom routes rather than a second service: nginx already terminates
TLS and proxies everything on this host to port 8811, so this costs no new
systemd unit, port, firewall rule, or dependency.
"""
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
@media(hover:none){.kbd{display:none}}
</style></head><body>
<header><b id="title">Review</b><span id="count"></span></header>
<main id="view"></main>
<nav>
  <button id="tab-review" class="on" onclick="go('review')">Review</button>
  <button id="tab-browse" onclick="go('browse')">Browse</button>
  <button id="tab-stats" onclick="go('stats')">Stats</button>
</nav>
<script>
const $=s=>document.querySelector(s), view=$('#view');
let tab='review', queue=[], idx=0, shown=false;
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const api=(p,o)=>fetch(p,o).then(r=>r.json());

function go(t){
  tab=t;
  for(const k of ['review','browse','stats']) $('#tab-'+k).classList.toggle('on',k===t);
  $('#title').textContent={review:'Review',browse:'Browse',stats:'Stats'}[t];
  ({review:loadReview,browse:loadBrowse,stats:loadStats}[t])();
}

async function loadReview(){
  view.innerHTML='<div class="empty">Loading…</div>';
  queue=await api('/api/due'); idx=0; shown=false; render();
}

function render(){
  const c=queue[idx];
  $('#count').textContent=queue.length?`${idx+1}/${queue.length}`:'';
  if(!c){
    view.innerHTML='<div class="empty">Nothing due. <br><br>🎉</div>';
    return;
  }
  view.innerHTML=`<div class="card">
    <div class="q">${esc(c.front)}</div>
    ${shown?`<div class="a">${esc(c.back)}</div>`:''}
    ${shown&&c.notes?`<div class="notes">📝 ${esc(c.notes)}</div>`:''}
    ${c.tags?`<div class="tags">🏷 ${esc(c.tags)}</div>`:''}
  </div>
  ${shown?`<div class="rate">
    <button onclick="rate(1)">🔴 Again</button>
    <button onclick="rate(3)">🟠 Hard</button>
    <button onclick="rate(4)">🟢 Good</button>
    <button onclick="rate(5)">🔵 Easy</button></div>
    <div class="kbd">1–4 to rate</div>`
   :`<button class="wide" onclick="reveal()">Reveal</button>
     <div class="kbd">space to reveal</div>`}`;
}

function reveal(){ shown=true; render(); }

async function rate(q){
  const c=queue[idx]; if(!c) return;
  shown=false;
  const r=await api('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:c.id,quality:q})});
  if(q===1) queue.push(c);           // Again — see it again this session
  idx++; render();
  if(r&&r.interval_days) $('#count').textContent=`${Math.min(idx+1,queue.length)}/${queue.length} · +${r.interval_days}d`;
}

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

document.onkeydown=e=>{
  if(tab!=='review') return;
  if(e.key===' '&&!shown){e.preventDefault();reveal();}
  else if(shown&&['1','2','3','4'].includes(e.key)) rate([1,3,4,5][+e.key-1]);
};
go('review');
</script></body></html>"""


def register(server) -> None:
    """Attach the web app to an MCPServer's Starlette app."""

    def chat_id():
        return db.get_registered_chat_id()

    @server.custom_route("/app", methods=["GET"])
    async def index(request: Request) -> Response:
        return HTMLResponse(INDEX)

    @server.custom_route("/api/due", methods=["GET"])
    async def due(request: Request) -> Response:
        cid = chat_id()
        if cid is None:
            return JSONResponse([])
        return JSONResponse([card_payload(c) for c in db.list_due_cards(cid)])

    @server.custom_route("/api/cards", methods=["GET"])
    async def cards(request: Request) -> Response:
        cid = chat_id()
        if cid is None:
            return JSONResponse([])
        term = request.query_params.get("q", "").strip()
        rows = db.search_cards(cid, term) if term else db.list_all_cards(cid)
        return JSONResponse([card_payload(c) for c in rows[:200]])

    @server.custom_route("/api/answer", methods=["POST"])
    async def answer(request: Request) -> Response:
        cid = chat_id()
        if cid is None:
            return JSONResponse({"error": "no registered chat"}, status_code=400)
        body = await request.json()
        try:
            card_id, quality = int(body["id"]), int(body["quality"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "id and quality required"}, status_code=400)
        if quality not in (1, 3, 4, 5):
            return JSONResponse({"error": "quality must be 1, 3, 4 or 5"}, status_code=400)
        card = apply_review(cid, card_id, quality)
        if card is None:
            return JSONResponse({"error": "card not found"}, status_code=404)
        return JSONResponse({"id": card["id"], "interval_days": card["interval_days"],
                             "due_at": card["due_at"]})

    @server.custom_route("/api/stats", methods=["GET"])
    async def stats(request: Request) -> Response:
        cid = chat_id()
        if cid is None:
            return JSONResponse({"error": "no registered chat"}, status_code=400)
        return JSONResponse({
            "deck": db.get_stats(cid),
            "retention": db.get_retention_stats(cid, days=30),
            "streak": db.get_streak_info(cid)["current"],
            "forecast": [{"day": d, "due": n} for d, n in db.get_forecast(cid, days=7)],
        })
