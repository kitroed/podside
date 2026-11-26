"""FastAPI app: HTTP routes, the /ws/{code} endpoint, broadcasting, and the HTML pages."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

import qr

from actions import handle_action
from models import COMMANDER_DAMAGE_LETHAL, DEFAULT_STARTING_LIFE, POISON_LETHAL, ActionError, Player, Room
from rooms import manager

log = logging.getLogger("mtg")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Broadcasting
# ---------------------------------------------------------------------------


async def send(ws: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def broadcast_state(room: Room) -> None:
    payload = {"type": "state", "state": room.snapshot()}
    dead = []
    for ws in list(room.connections):
        if not await send(ws, payload):
            dead.append(ws)
    for ws in dead:
        room.connections.discard(ws)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(manager.cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="MTG Life Tracker", lifespan=lifespan)


@app.post("/api/rooms")
async def create_room(starting_life: int = DEFAULT_STARTING_LIFE) -> dict[str, Any]:
    room = manager.create(starting_life=starting_life)
    return {"code": room.code, "display_url": f"/display/{room.code}", "play_url": f"/play/{room.code}"}


@app.get("/api/rooms/{code}")
async def get_room(code: str) -> dict[str, Any]:
    room = manager.get(code)
    if room is None:
        raise HTTPException(404, "Room not found")
    return room.snapshot()


@app.websocket("/ws/{code}")
async def room_socket(
    ws: WebSocket,
    code: str,
    role: str = Query("player", pattern="^(player|display)$"),
    token: str | None = None,
) -> None:
    room = manager.get(code)
    await ws.accept()
    if room is None:
        await send(ws, {"type": "error", "message": "Room not found"})
        await ws.close(code=4404)
        return

    player: Player | None = None

    # Reconnect: a known token reclaims the seat
    if role == "player" and token:
        player = room.player_by_token(token)
        if player:
            await send(ws, {"type": "welcome", "player_id": player.id, "token": player.token})

    room.connections.add(ws)
    room.last_activity = time.time()
    await send(ws, {"type": "state", "state": room.snapshot()})
    log.info("Room %s: %s connected (%d connections)", room.code, player.id if player else role, len(room.connections))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await send(ws, {"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get("type") == "ping":
                await send(ws, {"type": "pong"})
                continue

            async with room.lock:
                try:
                    if msg.get("type") == "join":
                        if role != "player":
                            raise ActionError("Only player connections can join")
                        if player is None:
                            player = room.add_player(msg.get("name", ""))
                        await send(ws, {"type": "welcome", "player_id": player.id, "token": player.token})
                    else:
                        if role == "player" and player is None:
                            raise ActionError("Join the room first")
                        handle_action(room, player, msg)
                except ActionError as e:
                    await send(ws, {"type": "error", "message": str(e)})
                    continue
                room.last_activity = time.time()
                await broadcast_state(room)

    except WebSocketDisconnect:
        pass
    finally:
        room.connections.discard(ws)
        log.info("Room %s: %s disconnected", room.code, player.id if player else role)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

# One color per seat, shared by the display and player views.
PLAYER_COLORS = ["#e5484d", "#3e9bff", "#30a46c", "#ffb224", "#a970ff", "#12b5b0", "#ff6bb5", "#c2c7d0"]

INDEX_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MTG tracker</title><style>body{font-family:system-ui;margin:2rem;background:#111;color:#eee}a{color:#8cf}</style></head>
<body><h1>MTG life tracker</h1><button onclick="go()">Create room</button><div id="out"></div>
<script>
async function go(){
  const r = await (await fetch('/api/rooms', {method:'POST'})).json();
  out.innerHTML = `<p>Room <b>${r.code}</b></p><p><a href="${r.display_url}" target="_blank">Table display</a></p>
    <p><a href="${r.play_url}" target="_blank">Player link</a> (open on each phone)</p>`;
}
</script></body></html>"""


# ---------------------------------------------------------------------------
# Display view (TV): read from across the room, with a tucked-away admin panel
# ---------------------------------------------------------------------------

DISPLAY_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Room __CODE__ · MTG tracker</title>
<style>
 :root{--bg:#0b0d10;--tile:#171a21;--fg:#f4f5f7;--dim:#9aa1ad;--warn:#ffb224;--lethal:#ff4d4f;--radius:1.6vh}
 *{box-sizing:border-box}
 html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
   font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
 body{display:grid;grid-template-rows:auto 1fr}
 button{font:inherit;color:inherit;cursor:pointer}

 header{display:flex;align-items:center;gap:3vh;padding:1.6vh 2.4vh}
 .join{flex:1;min-width:0;font-size:3.4vh;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .join b{color:var(--fg);font-weight:600}
 .join .bad{color:var(--warn)}
 .hqr{flex-shrink:0;width:9vh;height:9vh;border-radius:.6vh;display:block}
 .hqr[hidden]{display:none}
 .code{flex-shrink:0;font-size:6vh;font-weight:800;letter-spacing:.35em;margin-right:-.35em;line-height:1}
 .status{font-size:3vh;padding:.6vh 1.8vh;border-radius:99px;background:var(--warn);color:#000;font-weight:700}
 .status[hidden]{display:none}

 main{display:grid;gap:1.4vh;padding:0 2.4vh 2.4vh;min-height:0}
 .tile{position:relative;container-type:size;background:var(--tile);border-radius:var(--radius);
   border-top:1.4vh solid var(--c);display:flex;flex-direction:column;align-items:center;
   justify-content:space-between;padding:3cqh 4cqw;min-height:0;overflow:hidden;transition:background .6s}
 .tile.bump{background:#262b36;transition:none}
 .delta{position:absolute;top:4cqh;right:4cqw;font-size:min(15cqh,11cqw);font-weight:800;line-height:1;
   padding:.12em .35em;border-radius:.3em;background:#0b0d10cc;font-variant-numeric:tabular-nums;
   animation:delta-fade var(--ms) linear forwards;animation-delay:var(--age)}
 .delta.loss{color:var(--lethal)}.delta.gain{color:#3dd68c}
 .delta small{font-size:.45em;font-weight:700;color:var(--dim);margin-left:.25em}
 @keyframes delta-fade{0%,75%{opacity:1}100%{opacity:0}}
 .name{font-size:min(13cqh,11cqw);font-weight:700;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .life{font-size:min(48cqh,40cqw);font-weight:800;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
 .life.low{color:var(--warn)}
 .extras{display:flex;flex-wrap:wrap;justify-content:center;gap:2cqh 3cqw;font-size:min(9cqh,7.5cqw);min-height:1.2em}
 .pill{display:inline-flex;align-items:center;gap:.35em;padding:.1em .55em;border-radius:.5em;background:#232733;font-weight:600}
 .pill .k{color:var(--dim);font-weight:500}
 .pill.cmd{border-left:.35em solid var(--src)}
 .pill.warn{box-shadow:inset 0 0 0 .12em var(--warn)}
 .pill.lethal{background:var(--lethal);color:#000}.pill.lethal .k{color:#000}

 .tile.out{background:#111318;border-top-color:#3a3f4a}
 .tile.out .name{color:var(--dim)}
 .tile.out .life{color:var(--lethal);letter-spacing:.06em}
 .tile.out .pill{background:#2a1416;color:var(--fg)}

 .empty{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3vh;text-align:center}
 .empty .big{font-size:8vh;font-weight:800}
 .empty .url{font-size:5vh;color:var(--dim)}.empty .url b{color:var(--fg)}
 .empty .qr{width:min(40vh,40vw);height:min(40vh,40vw);border-radius:1.4vh}

 .gear{position:fixed;right:2vh;bottom:2vh;width:7vh;height:7vh;border-radius:50%;border:0;
   background:#ffffff14;font-size:3.6vh;opacity:.35}
 .gear:hover,.gear:focus-visible{opacity:1}
 dialog{background:#1b1f27;color:var(--fg);border:0;border-radius:var(--radius);padding:3vh;min-width:min(60vh,90vw)}
 dialog::backdrop{background:#000a}
 dialog h2{margin:0 0 2vh;font-size:4vh}
 .row{display:flex;align-items:center;gap:1.6vh;margin:1.6vh 0;font-size:3vh}
 .row .lbl{flex:1}
 .btn{min-height:6.5vh;min-width:6.5vh;padding:0 2.4vh;border-radius:1vh;border:0;background:#2c3240;font-size:3vh;font-weight:600}
 .btn.primary{background:#3e63dd}.btn.danger{background:#a4282b}
 .btn[aria-pressed="true"]{background:#30a46c}
 .val{min-width:3ch;text-align:center;font-weight:700;font-variant-numeric:tabular-nums}

 .toast{position:fixed;left:50%;bottom:3vh;transform:translateX(-50%);background:#2c3240;padding:1.6vh 3vh;
   border-radius:1vh;font-size:3vh;max-width:90vw;box-shadow:0 1vh 4vh #0008}
 .toast[hidden]{display:none}
</style></head>
<body>
<header>
  <div class="join" id="join"></div>
  <div class="status" id="status" hidden>reconnecting…</div>
  <div class="code" aria-label="Room code">__CODE__</div>
  <img class="hqr" id="hqr" alt="QR code to join" hidden>
</header>
<main id="tiles"></main>

<button class="gear" id="gear" aria-label="Table settings">⚙</button>
<dialog id="panel">
  <h2>Table</h2>
  <div class="row"><span class="lbl">Undo last change</span><button class="btn primary" data-act="undo">Undo</button></div>
  <div class="row"><span class="lbl">Anyone can edit anyone</span><button class="btn" id="edits" aria-pressed="false">Off</button></div>
  <div class="row"><span class="lbl">Starting life</span>
    <button class="btn" data-life="-1" aria-label="Lower starting life">−</button>
    <span class="val" id="startLife">40</span>
    <button class="btn" data-life="1" aria-label="Raise starting life">+</button></div>
  <div class="row"><span class="lbl">New game, same players</span><button class="btn danger" id="reset">New game</button></div>
  <div class="row"><span class="lbl">Full screen</span><button class="btn" id="fs">Full screen</button>
    <button class="btn" id="close">Close</button></div>
</dialog>
<div class="toast" id="toast" hidden></div>

<script>
"use strict";
const CODE = "__CODE__";
const POISON_LETHAL = __POISON_LETHAL__, CMD_LETHAL = __CMD_LETHAL__;
const COLORS = __COLORS__;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const colorOf = pid => COLORS[(parseInt(pid.slice(1), 10) - 1) % COLORS.length];
const STARTING_LIFE_PRESETS = [20, 25, 30, 40];
// The server draws the QR code; we pass the address this page was opened at so it matches the proxy's domain.
const QR_SRC = `/api/rooms/${CODE}/qr.svg?base=${encodeURIComponent(location.origin)}`;
$("hqr").src = QR_SRC;

let ws = null, state = null, retry = 0, prevLife = {}, resetArmed = null;
// Running total for the latest action, e.g. "−5" after five quick taps (the server groups them).
const DELTA_MS = 2500;
let seen = null, delta = null, deltaTimer = null;

// ---- connection -------------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${CODE}?role=display`);
  ws.onopen = () => { retry = 0; $("status").hidden = true; };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === "state") { state = m.state; trackDelta(); render(); }
    else if (m.type === "error") toast(m.message);
  };
  ws.onclose = () => {
    $("status").hidden = false;
    setTimeout(connect, Math.min(1000 * 2 ** retry++, 8000));
  };
}
const act = msg => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); else toast("Not connected"); };

// ---- rendering ----------------------------------------------------------------

function columns(n) { return n <= 2 ? Math.max(n, 1) : n <= 4 ? 2 : n <= 6 ? 3 : 4; }

function outReason(p, names) {
  if (p.life <= 0) return `Life ${p.life}`;
  if (p.poison >= POISON_LETHAL) return `Poison ${p.poison}`;
  const [src, d] = Object.entries(p.cmd_damage).find(([, d]) => d >= CMD_LETHAL) || [];
  if (!src) return "";
  return src === p.id ? `Own commander ${d}` : `${names[src] || src}'s commander ${d}`;
}

function tile(p, names) {
  const extras = [];
  if (p.poison) {
    const cls = p.poison >= POISON_LETHAL ? "lethal" : p.poison >= POISON_LETHAL - 3 ? "warn" : "";
    extras.push(`<span class="pill poison ${cls}"><span class="k">Poison</span>${p.poison}</span>`);
  }
  for (const [src, d] of Object.entries(p.cmd_damage)) {
    const cls = d >= CMD_LETHAL ? "lethal" : d >= CMD_LETHAL - 6 ? "warn" : "";
    const label = src === p.id ? "Own" : esc(names[src] || src);
    extras.push(`<span class="pill cmd ${cls}" style="--src:${colorOf(src)}"><span class="k">${label}</span>${d}</span>`);
  }
  for (const [name, n] of Object.entries(p.counters)) {
    extras.push(`<span class="pill"><span class="k">${esc(name[0].toUpperCase() + name.slice(1))}</span>${n}</span>`);
  }
  const out = p.eliminated, low = !out && p.life <= 10 ? "low" : "";
  const bottom = out ? `<span class="pill">${esc(outReason(p, names))}</span>` : extras.join("");
  return `<section class="tile ${out ? "out" : ""}" data-pid="${esc(p.id)}" style="--c:${colorOf(p.id)}">
    <div class="name">${esc(p.name)}</div>
    <div class="life ${low}">${out ? "OUT" : p.life}</div>
    <div class="extras">${bottom}</div>
    ${delta && delta.pid === p.id ? deltaHtml() : ""}
  </section>`;
}

const signed = n => (n > 0 ? "+" : "−") + Math.abs(n);

function trackDelta() {
  // Show a bubble only for a new action or a growing group, never for an undo or on (re)connect.
  const la = state.last_action;
  const key = la && {seq: la.seq, delta: la.action.delta};
  const fresh = seen && la && (la.seq > seen.seq || (la.seq === seen.seq && la.action.delta !== seen.delta));
  if (key && (!seen || key.seq >= seen.seq)) seen = key;
  if (!fresh) {
    // Drop a bubble whose action was just undone; keep it through unrelated updates like renames.
    if (delta && !(la && la.seq === delta.seq && la.action.delta === delta.d)) { delta = null; clearTimeout(deltaTimer); }
    return;
  }
  const a = la.action, d = Number(a.delta);
  const [text, gain] = a.type === "life" ? [signed(d), d > 0]
    : a.type === "cmd_damage" ? [`${signed(-d)}<small>cmd</small>`, d < 0]
    : a.type === "poison" ? [`${signed(d)}<small>poison</small>`, d < 0]
    : [`${signed(d)}<small>${esc(a.name)}</small>`, d > 0];
  delta = {pid: a.target, text, gain, start: Date.now(), seq: la.seq, d: a.delta};
  clearTimeout(deltaTimer);
  deltaTimer = setTimeout(() => { delta = null; render(); }, DELTA_MS);
}

function deltaHtml() {
  const age = Date.now() - delta.start;  // keeps the fade in step when tiles re-render
  return `<div class="delta ${delta.gain ? "gain" : "loss"}" style="--ms:${DELTA_MS}ms;--age:-${age}ms">${delta.text}</div>`;
}

function render() {
  const main = $("tiles"), players = state.players;
  if (delta && !players.some(p => p.id === delta.pid)) delta = null;
  const names = Object.fromEntries(players.map(p => [p.id, p.name]));
  if (!players.length) {
    main.style.gridTemplateColumns = "1fr";
    main.innerHTML = `<div class="empty"><div class="big">Scan to join</div>
      <img class="qr" src="${QR_SRC}" alt="QR code to join">
      <div class="url">or go to ${joinHtml()}</div></div>`;
  } else {
    const cols = columns(players.length);
    main.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    main.style.gridTemplateRows = `repeat(${Math.ceil(players.length / cols)}, minmax(0, 1fr))`;
    main.innerHTML = players.map(p => tile(p, names)).join("");
    for (const p of players) {
      if (p.id in prevLife && prevLife[p.id] !== p.life) bump(p.id);
    }
  }
  prevLife = Object.fromEntries(players.map(p => [p.id, p.life]));
  $("join").innerHTML = players.length ? `Join at ${joinHtml()}` : "";
  $("hqr").hidden = !players.length;  // the empty screen shows a big one instead
  $("edits").setAttribute("aria-pressed", state.table_edits);
  $("edits").textContent = state.table_edits ? "On" : "Off";
  $("startLife").textContent = state.starting_life;
}

function bump(pid) {
  const el = document.querySelector(`.tile[data-pid="${CSS.escape(pid)}"]`);
  if (!el) return;
  el.classList.add("bump");
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("bump")));
}

function joinHtml() {
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  const url = `<b>${esc(location.host)}/play/${CODE}</b>`;
  return local ? `${url} <span class="bad">(phones can't reach localhost)</span>` : url;
}

// ---- toast ------------------------------------------------------------------

let toastTimer = null;
function toast(msg, ms = 4000) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

// ---- admin panel --------------------------------------------------------------

$("gear").onclick = () => $("panel").showModal();
$("close").onclick = () => $("panel").close();
document.querySelector('[data-act="undo"]').onclick = () => act({type: "undo"});
$("edits").onclick = () => act({type: "settings", table_edits: !state.table_edits});
for (const b of document.querySelectorAll("[data-life]")) {
  b.onclick = () => {
    // Step through common formats rather than one point at a time.
    const cur = state.starting_life, up = b.dataset.life === "1";
    const next = up ? STARTING_LIFE_PRESETS.find(v => v > cur) : [...STARTING_LIFE_PRESETS].reverse().find(v => v < cur);
    if (next) act({type: "settings", starting_life: next});
  };
}
$("reset").onclick = () => {
  const b = $("reset");
  if (resetArmed) {
    clearTimeout(resetArmed); resetArmed = null; b.textContent = "New game";
    act({type: "reset"}); $("panel").close();
    return;
  }
  b.textContent = "Tap again";
  resetArmed = setTimeout(() => { resetArmed = null; b.textContent = "New game"; }, 3000);
};
$("fs").onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => toast("Full screen isn't available here"));
};

// ---- wake lock ------------------------------------------------------------------

let wakeLock = null;
async function keepAwake() {
  if (!("wakeLock" in navigator)) return false;
  if (wakeLock && !wakeLock.released) return true;
  try { wakeLock = await navigator.wakeLock.request("screen"); return true; }
  catch { return false; }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });
document.addEventListener("click", keepAwake);  // some browsers only grant it after a tap
keepAwake().then(ok => {
  if (!ok && !window.isSecureContext) toast("This screen may sleep: keeping it awake needs HTTPS or localhost", 10000);
});

connect();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Player view (phone): one-handed, big targets, hold to repeat
# ---------------------------------------------------------------------------

PLAYER_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0d10">
<title>Room __CODE__ · MTG tracker</title>
<style>
 :root{--bg:#0b0d10;--card:#171a21;--btn:#262b36;--fg:#f4f5f7;--dim:#9aa1ad;--warn:#ffb224;--lethal:#ff4d4f;--c:#343a48}
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 html,body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
 body{min-height:100dvh;display:flex;flex-direction:column;padding:env(safe-area-inset-top) 0 0}
 button,input{font:inherit;color:inherit}
 button{border:0;border-radius:14px;background:var(--btn);font-weight:700;cursor:pointer;
   user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;touch-action:manipulation}
 button:active,button.held{filter:brightness(1.5)}
 button:disabled{opacity:.4}
 [hidden]{display:none!important}

 header{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:4px solid var(--c)}
 .me{flex:1;min-width:0;min-height:48px;text-align:left;background:none;padding:0;font-size:22px;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .me small{color:var(--dim);font-weight:500;font-size:16px}
 .room{color:var(--dim);font-size:16px;font-weight:600;letter-spacing:.15em}
 .status{font-size:14px;font-weight:700;padding:4px 10px;border-radius:99px;background:var(--warn);color:#000}

 main{flex:1;display:flex;flex-direction:column;gap:14px;padding:14px 16px 96px}
 .life{text-align:center;font-size:min(34vw,170px);font-weight:800;line-height:1;
   font-variant-numeric:tabular-nums;padding:8px 0;letter-spacing:-.02em}
 .life.low{color:var(--warn)}
 .outnote{text-align:center;color:var(--lethal);font-weight:800;font-size:22px;margin-top:-6px}
 .who{display:flex;gap:8px;overflow-x:auto;padding-bottom:2px;scrollbar-width:none}
 .who::-webkit-scrollbar{display:none}
 .tab{flex:0 0 auto;min-width:72px;min-height:56px;padding:4px 14px;display:flex;flex-direction:column;
   align-items:center;justify-content:center;background:var(--card);border:2px solid transparent;border-top:4px solid var(--tc)}
 .tab .n{max-width:9ch;font-size:15px;font-weight:600;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .tab .l{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}
 .tab[aria-pressed="true"]{border-color:var(--tc);background:#232733}
 .tab[aria-pressed="true"] .n{color:var(--fg)}
 .target{text-align:center;font-size:20px;font-weight:700;color:var(--c);margin-bottom:-8px;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .pad{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 .pad button{font-size:40px;height:96px}
 .pad .small{font-size:26px;height:64px}
 .minus{background:#3a1f24}.plus{background:#1d3327}

 .card{background:var(--card);border-radius:16px;padding:6px 14px}
 .card h2{margin:10px 0 4px;font-size:15px;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:.08em}
 .row{display:flex;align-items:center;gap:10px;min-height:64px}
 .row + .row{border-top:1px solid #ffffff10}
 .row .lbl{flex:1;min-width:0;font-size:20px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .row .dot{display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--src);margin-right:8px}
 .row .own{color:var(--dim);font-size:17px}
 .reveal{display:block;width:100%;min-height:52px;padding:0;background:none;border-top:1px solid #ffffff10;
   border-radius:0;color:var(--dim);font-size:17px;font-weight:600;text-align:left}
 .row .val{min-width:2.2ch;text-align:center;font-size:28px;font-weight:800;font-variant-numeric:tabular-nums}
 .row .val.warn{color:var(--warn)}.row .val.lethal{color:var(--lethal)}
 .row button{width:56px;height:56px;font-size:28px}
 .none{color:var(--dim);font-size:17px;padding:14px 0}

 footer{position:fixed;left:0;right:0;bottom:0;padding:10px 16px calc(10px + env(safe-area-inset-bottom));
   background:linear-gradient(#0b0d1000,var(--bg) 30%)}
 .undo{width:100%;height:60px;font-size:19px;background:#2c3240;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 16px}

 .join{flex:1;display:flex;flex-direction:column;justify-content:center;gap:16px;padding:24px 16px}
 .join h1{margin:0;font-size:30px}
 .join p{margin:0;color:var(--dim);font-size:18px}
 .join input{height:64px;border-radius:14px;border:2px solid #343a48;background:var(--card);padding:0 16px;font-size:24px}
 .join input:focus{outline:none;border-color:#3e63dd}
 .join button{height:64px;font-size:24px;background:#3e63dd}
 .msg{flex:1;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px;font-size:22px;color:var(--dim)}

 .toast{position:fixed;left:16px;right:16px;bottom:84px;background:#3a2a10;border:1px solid var(--warn);
   padding:14px 16px;border-radius:12px;font-size:17px;text-align:center}
</style></head>
<body>
<header>
  <button class="me" id="me" aria-label="Rename">MTG tracker</button>
  <span class="status" id="status" hidden>reconnecting…</span>
  <span class="room">__CODE__</span>
</header>

<div class="msg" id="loading">Connecting…</div>

<form class="join" id="join" hidden>
  <h1>Join room __CODE__</h1>
  <p>Your name shows on the table display.</p>
  <input id="name" maxlength="24" autocomplete="nickname" placeholder="Your name" enterkeyhint="go">
  <button type="submit">Join</button>
</form>

<main id="game" hidden>
  <nav class="who" id="who" aria-label="Whose totals to change" hidden></nav>
  <div>
    <div class="target" id="target" hidden></div>
    <div class="life" id="life">40</div>
    <div class="outnote" id="outnote" hidden></div>
  </div>
  <div class="pad">
    <button class="minus" data-hold='{"type":"life","delta":-1}' aria-label="Lose 1 life">−1</button>
    <button class="plus" data-hold='{"type":"life","delta":1}' aria-label="Gain 1 life">+1</button>
    <button class="minus small" data-hold='{"type":"life","delta":-5}' aria-label="Lose 5 life">−5</button>
    <button class="plus small" data-hold='{"type":"life","delta":5}' aria-label="Gain 5 life">+5</button>
  </div>
  <section class="card">
    <div class="row">
      <span class="lbl">Poison</span>
      <button data-hold='{"type":"poison","delta":-1}' aria-label="Remove poison">−</button>
      <span class="val" id="poison">0</span>
      <button data-hold='{"type":"poison","delta":1}' aria-label="Add poison">+</button>
    </div>
  </section>
  <section class="card">
    <h2 id="cmdTitle">Commander damage taken</h2>
    <div id="cmd"></div>
  </section>
</main>

<footer id="footer" hidden><button class="undo" id="undo" disabled>Nothing to undo</button></footer>
<div class="toast" id="toast" hidden></div>

<script>
"use strict";
const CODE = "__CODE__";
const POISON_LETHAL = __POISON_LETHAL__, CMD_LETHAL = __CMD_LETHAL__;
const COLORS = __COLORS__;
const TOKEN_KEY = "mtg-token-" + CODE;
const HOLD_DELAY = 400, HOLD_EVERY = 120;  // ms before repeating, then between repeats

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const colorOf = pid => COLORS[(parseInt(pid.slice(1), 10) - 1) % COLORS.length];
const signed = n => (n > 0 ? "+" : "−") + Math.abs(n);
const store = {
  get() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set(v) { try { localStorage.setItem(TOKEN_KEY, v); } catch {} },
};

let ws = null, state = null, me = null, retry = 0, gone = false, cmdKey = null, whoKey = null;
let viewing = null;  // player whose totals the buttons change when table edits are on; null means me
const ownShown = new Set();  // players whose own-commander row this phone has revealed

function viewId() {
  if (viewing && state.table_edits && state.players.some(x => x.id === viewing)) return viewing;
  return me;
}
// Buttons carry the action; the target is whoever is selected when the press starts.
const msgFor = btn => ({...JSON.parse(btn.dataset.hold), target: viewId()});

// ---- connection -------------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const token = store.get();
  ws = new WebSocket(`${proto}://${location.host}/ws/${CODE}?role=player` + (token ? `&token=${encodeURIComponent(token)}` : ""));
  ws.onopen = () => { retry = 0; $("status").hidden = true; };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === "welcome") { me = m.player_id; store.set(m.token); }
    else if (m.type === "state") { state = m.state; render(); }
    else if (m.type === "error") {
      if (m.message === "Room not found") gone = true;
      toast(m.message);
    }
  };
  ws.onclose = () => {
    endPress(false);
    if (gone) { show("msg"); $("loading").textContent = "This room has ended. Ask for a new link."; return; }
    $("status").hidden = false;
    setTimeout(connect, Math.min(1000 * 2 ** retry++, 8000));
  };
}

function act(msg) {
  if (ws && ws.readyState === 1) { ws.send(JSON.stringify(msg)); return true; }
  toast("Not connected yet");
  return false;
}

// ---- rendering ----------------------------------------------------------------

function show(which) {
  $("loading").hidden = which !== "msg";
  $("join").hidden = which !== "join";
  $("game").hidden = $("footer").hidden = which !== "game";
}

function render() {
  const p = me && state.players.find(x => x.id === me);
  if (!p) {
    $("me").textContent = "MTG tracker";
    if ($("join").hidden) { show("join"); }
    return;
  }
  show("game");
  $("me").innerHTML = `${esc(p.name)} <small>✎</small>`;
  if (!state.table_edits) viewing = null;
  renderTabs();

  const v = state.players.find(x => x.id === viewId());  // whose totals are on screen
  const mine = v.id === me;
  document.documentElement.style.setProperty("--c", colorOf(v.id));
  $("target").hidden = mine;
  $("target").textContent = `${v.name}'s life`;
  $("life").textContent = v.life;
  $("life").className = "life" + (!v.eliminated && v.life <= 10 ? " low" : "");
  $("outnote").hidden = !v.eliminated;
  $("outnote").textContent = mine ? "You're out" : `${v.name} is out`;
  $("poison").textContent = v.poison;
  $("poison").className = "val" + (v.poison >= POISON_LETHAL ? " lethal" : v.poison >= POISON_LETHAL - 3 ? " warn" : "");
  $("cmdTitle").textContent = mine ? "Commander damage taken" : `Commander damage ${v.name} took`;

  // One row per commander that can hit them. Their own commander (rare: an opponent stole it)
  // is last and hidden behind a link until revealed or until it has damage.
  // Rebuild rows only when the players change, so a held button isn't replaced mid-hold.
  const others = state.players.filter(x => x.id !== v.id);
  const key = v.id + "/" + state.players.map(o => `${o.id}:${o.name}`).join("|");
  if (key !== cmdKey) {
    cmdKey = key;
    const row = (o, label, aria, id = "") => `<div class="row" ${id} style="--src:${colorOf(o.id)}">
      <span class="lbl"><span class="dot"></span>${label}</span>
      <button data-hold='{"type":"cmd_damage","source":"${o.id}","delta":-1}' aria-label="Less damage from ${aria}">−</button>
      <span class="val" data-src="${o.id}">0</span>
      <button data-hold='{"type":"cmd_damage","source":"${o.id}","delta":1}' aria-label="More damage from ${aria}">+</button>
    </div>`;
    $("cmd").innerHTML = others.map(o => row(o, o.id === me ? "You" : esc(o.name), esc(o.name))).join("")
      + row(v, `<span class="own">${mine ? "Own" : `${esc(v.name)}'s own`}</span>`, "own commander", 'id="ownRow"')
      + `<button class="reveal" id="ownReveal">Own commander damage…</button>`
      + (others.length ? "" : `<div class="none">Other players appear here when they join.</div>`);
  }
  if ((v.cmd_damage[v.id] || 0) > 0) ownShown.add(v.id);  // sticky, so it can't vanish mid-use
  $("ownRow").hidden = !ownShown.has(v.id);
  $("ownReveal").hidden = ownShown.has(v.id);
  for (const el of $("cmd").querySelectorAll(".val[data-src]")) {
    const d = v.cmd_damage[el.dataset.src] || 0;
    el.textContent = d;
    el.className = "val" + (d >= CMD_LETHAL ? " lethal" : d >= CMD_LETHAL - 6 ? " warn" : "");
  }

  const last = state.last_action;
  $("undo").disabled = !last;
  $("undo").textContent = last ? `Undo: ${describe(last)}` : "Nothing to undo";
}

function renderTabs() {
  const who = $("who");
  const players = [...state.players].sort((a, b) => (b.id === me) - (a.id === me));  // me first
  who.hidden = !state.table_edits || players.length < 2;
  if (who.hidden) { whoKey = null; return; }
  const key = viewId() + "/" + players.map(x => `${x.id}:${x.name}`).join("|");
  if (key !== whoKey) {  // rebuild only on change so a tap in progress isn't lost
    whoKey = key;
    who.innerHTML = players.map(x => `<button class="tab" data-pid="${x.id}" aria-pressed="${x.id === viewId()}"
      style="--tc:${colorOf(x.id)}"><span class="n">${x.id === me ? "You" : esc(x.name)}</span><span class="l" data-life="${x.id}"></span></button>`).join("");
  }
  for (const x of players) who.querySelector(`[data-life="${x.id}"]`).textContent = x.life;
}

$("cmd").addEventListener("click", e => {
  if (!e.target.closest("#ownReveal")) return;
  ownShown.add(viewId());
  render();
});

$("who").onclick = e => {
  const tab = e.target.closest("[data-pid]");
  if (!tab) return;
  viewing = tab.dataset.pid === me ? null : tab.dataset.pid;
  render();
};

function nameOf(pid) {
  if (pid === "display") return "Table";
  const p = state.players.find(x => x.id === pid);
  return p ? p.name : pid;
}

function describe({actor, action: a}) {
  const who = nameOf(a.target || actor), d = Number(a.delta);
  if (a.type === "life") return `${who} ${signed(d)} life`;
  if (a.type === "poison") return `${who} ${signed(d)} poison`;
  if (a.type === "cmd_damage") {
    if (a.source === (a.target || actor)) return `${who} ${signed(d)} own cmd damage`;
    return `${who} ${signed(d)} cmd damage from ${nameOf(a.source)}`;
  }
  if (a.type === "counter") return `${who} ${signed(d)} ${a.name}`;
  return a.type;
}

// ---- taps and holds ----------------------------------------------------------------
// A tap acts on release, so a finger that lands on a button to scroll does nothing:
// moving past MOVE_SLOP, or the browser cancelling the press as the page scrolls, drops it.
// Holding still for HOLD_DELAY starts repeating instead.

const MOVE_SLOP = 10;  // px a finger can drift and still count as a tap
let press = null;

function startPress(btn, e) {
  endPress(false);
  press = {btn, id: e.pointerId, x: e.clientX, y: e.clientY, msg: msgFor(btn), repeating: false};
  btn.classList.add("held");
  press.timer = setTimeout(() => {
    press.repeating = true;
    if (!act(press.msg)) { endPress(false); return; }
    navigator.vibrate?.(8);
    press.timer = setInterval(() => { if (!act(press.msg)) endPress(false); }, HOLD_EVERY);
  }, HOLD_DELAY);
}

function endPress(isTap) {
  if (!press) return;
  const {btn, msg, repeating, timer} = press;
  press = null;
  clearTimeout(timer); clearInterval(timer);
  btn.classList.remove("held");
  if (isTap && !repeating && act(msg)) navigator.vibrate?.(8);
}

document.addEventListener("pointerdown", e => {
  const btn = e.target.closest("[data-hold]");
  if (btn && e.button === 0) startPress(btn, e);
});
document.addEventListener("pointermove", e => {
  if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > MOVE_SLOP) endPress(false);
});
document.addEventListener("pointerup", e => { if (press && e.pointerId === press.id) endPress(true); });
document.addEventListener("pointercancel", () => endPress(false));
document.addEventListener("visibilitychange", () => endPress(false));
document.addEventListener("contextmenu", e => { if (e.target.closest("[data-hold]")) e.preventDefault(); });
// Keyboard and screen readers: Enter/Space acts once.
document.addEventListener("click", e => {
  const btn = e.target.closest("[data-hold]");
  if (btn && e.detail === 0) act(msgFor(btn));
});

// ---- other controls ------------------------------------------------------------

$("join").onsubmit = e => {
  e.preventDefault();
  const name = $("name").value.trim();
  if (!name) { $("name").focus(); return; }
  act({type: "join", name});
};
$("undo").onclick = () => act({type: "undo"});
$("me").onclick = () => {
  const p = me && state && state.players.find(x => x.id === me);
  if (!p) return;
  const name = prompt("Your name", p.name);
  if (name && name.trim() && name.trim() !== p.name) act({type: "rename", name: name.trim()});
};

let toastTimer = null;
function toast(msg) {
  $("toast").textContent = msg; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
}

connect();
</script>
</body></html>"""


def _render(page: str, code: str) -> str:
    return (
        page.replace("__CODE__", code)
        .replace("__POISON_LETHAL__", str(POISON_LETHAL))
        .replace("__CMD_LETHAL__", str(COMMANDER_DAMAGE_LETHAL))
        .replace("__COLORS__", json.dumps(PLAYER_COLORS))
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_PAGE


def _room_code(code: str) -> str:
    # Only codes of existing rooms get through, so they're safe to put in the page.
    room = manager.get(code)
    if room is None:
        raise HTTPException(404, "Room not found")
    return room.code


@app.get("/display/{code}", response_class=HTMLResponse)
async def display_page(code: str) -> str:
    return _render(DISPLAY_PAGE, _room_code(code))


@app.get("/play/{code}", response_class=HTMLResponse)
async def play_page(code: str) -> str:
    return _render(PLAYER_PAGE, _room_code(code))


@app.get("/api/rooms/{code}/qr.svg")
async def room_qr(
    code: str,
    base: str = Query(max_length=200, pattern=r"^https?://[A-Za-z0-9.\-\[\]:]+$"),
) -> Response:
    """QR code for {base}/play/{code}. The display passes its own origin as base."""
    svg = qr.svg(f"{base}/play/{_room_code(code)}")
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})

