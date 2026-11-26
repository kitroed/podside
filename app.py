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


app = FastAPI(title="EDH Table", lifespan=lifespan)


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
                        if player is None or player.id not in room.players:  # new, or removed and rejoining
                            player = room.add_player(msg.get("name", ""), msg.get("colors"))
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

# Deck color pips (W U B R G, C for colorless), shared by the display and player pages.
PIP_CSS = """ .pips{display:inline-flex;gap:.16em;flex-shrink:0}
 .pip{width:1.25em;height:1.25em;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;
   font-size:.62em;font-weight:800;line-height:1;box-shadow:inset 0 0 0 .1em #0006}
 .pip-W{background:#f3ecd0;color:#2a2520}.pip-U{background:#2f7fd0;color:#fff}
 .pip-B{background:#1d1917;color:#eee;box-shadow:inset 0 0 0 .1em #a89f96}
 .pip-R{background:#d94a3a;color:#fff}.pip-G{background:#2e9a5b;color:#fff}.pip-C{background:#b9b3ad;color:#2a2520}
"""
PIP_JS = """const COLOR_NAMES = {W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless"};
const pips = c => !c ? "" : `<span class="pips" role="img" aria-label="${[...c].map(x => COLOR_NAMES[x]).join(", ")}">`
  + [...c].map(x => `<span class="pip pip-${x}" aria-hidden="true">${x}</span>`).join("") + "</span>";
"""

INDEX_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0d10">
<meta name="description" content="Life totals on the big screen, controls on every phone. Built for EDH pods.">
<title>EDH Table</title>
<style>
__PIP_CSS__ :root{--bg:#0b0d10;--card:#171a21;--fg:#f4f5f7;--dim:#9aa1ad;--accent:#3e63dd;--warn:#ffb224}
 *{box-sizing:border-box}
 html,body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
 body{min-height:100dvh;display:flex;flex-direction:column;align-items:center;justify-content:center;
   padding:40px 16px;background:radial-gradient(ellipse 90% 60% at 50% 0%,#1b2233 0%,var(--bg) 70%)}
 .hero{text-align:center;margin-bottom:clamp(28px,6vh,64px)}
 .hero .pips{font-size:clamp(30px,4.5vw,60px);gap:.35em;margin-bottom:.35em}
 h1{margin:0;font-size:clamp(52px,9vw,136px);font-weight:900;letter-spacing:-.02em;line-height:1}
 .tag{margin:.55em 0 0;font-size:clamp(18px,2.4vw,30px);color:var(--dim)}
 .cards{display:grid;grid-template-columns:repeat(2,minmax(0,440px));gap:24px;width:100%;justify-content:center}
 .card{background:var(--card);border-radius:20px;padding:28px;display:flex;flex-direction:column;gap:14px}
 .card h2{margin:0;font-size:26px}
 .card p{margin:0;color:var(--dim);font-size:17px;line-height:1.45}
 .btn{min-height:64px;border:0;border-radius:14px;font:inherit;font-size:22px;font-weight:800;color:#fff;
   background:var(--accent);cursor:pointer;margin-top:auto}
 .btn:disabled{opacity:.5;cursor:default}
 .code{display:flex;gap:10px;margin-top:auto}
 .code input{flex:1;min-width:0;height:64px;border-radius:14px;border:2px solid #343a48;background:#0f1218;color:var(--fg);
   font:inherit;font-size:32px;font-weight:800;letter-spacing:.3em;text-align:center;text-transform:uppercase}
 .code input::placeholder{color:#3a404c}
 .code input:focus{outline:none;border-color:var(--accent)}
 .code .btn{padding:0 24px;margin-top:0}
 .err{color:var(--warn);font-size:16px;min-height:1.3em}
 @media (max-width:760px){.cards{grid-template-columns:minmax(0,480px)}.join{order:-1}}
</style></head>
<body>
<header class="hero">
  <div class="pips" aria-hidden="true"><span class="pip pip-W">W</span><span class="pip pip-U">U</span><span class="pip pip-B">B</span><span class="pip pip-R">R</span><span class="pip pip-G">G</span></div>
  <h1>EDH Table</h1>
  <p class="tag">Life totals on the big screen. Controls on every phone.</p>
</header>
<main class="cards">
  <section class="card start">
    <h2>Start a game</h2>
    <p>Open this on the TV. It becomes the scoreboard, with a QR code for everyone to join.</p>
    <button class="btn" id="start">Start a game</button>
    <p class="err" id="startErr" role="alert"></p>
  </section>
  <section class="card join">
    <h2>Join a game</h2>
    <p>Scan the QR code on the TV, or type the room code.</p>
    <form class="code" id="joinForm">
      <input id="code" maxlength="4" autocomplete="off" autocapitalize="characters" spellcheck="false"
        placeholder="ABCD" aria-label="Room code" enterkeyhint="go">
      <button class="btn" type="submit">Join</button>
    </form>
    <p class="err" id="joinErr" role="alert"></p>
  </section>
</main>
<script>
"use strict";
const $ = id => document.getElementById(id);

$("start").onclick = async () => {
  $("start").disabled = true;
  $("startErr").textContent = "";
  try {
    const r = await fetch("/api/rooms", {method: "POST"});
    if (!r.ok) throw new Error(r.status);
    location.href = (await r.json()).display_url;  // this screen becomes the scoreboard
  } catch {
    $("startErr").textContent = "Couldn't start a game. Check the connection and try again.";
    $("start").disabled = false;
  }
};
addEventListener("pageshow", () => { $("start").disabled = false; });  // after Back from the scoreboard

$("code").addEventListener("input", e => {
  e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4);
  $("joinErr").textContent = "";
});
$("joinForm").onsubmit = async e => {
  e.preventDefault();
  const code = $("code").value;
  if (code.length !== 4) { $("joinErr").textContent = "Room codes are 4 letters."; return; }
  try {
    const r = await fetch(`/api/rooms/${code}`);
    if (r.status === 404) { $("joinErr").textContent = `No game with code ${code}. Check the TV.`; return; }
    if (!r.ok) throw new Error(r.status);
    location.href = `/play/${code}`;
  } catch {
    $("joinErr").textContent = "Couldn't reach the server. Try again.";
  }
};
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Display view (TV): read from across the room, with a tucked-away admin panel
# ---------------------------------------------------------------------------

DISPLAY_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Room __CODE__ · EDH Table</title>
<style>
__PIP_CSS__ :root{--bg:#0b0d10;--tile:#171a21;--fg:#f4f5f7;--dim:#9aa1ad;--warn:#ffb224;--lethal:#ff4d4f;--radius:1.6vh}
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
 .delta{position:absolute;top:4cqh;left:4cqw;font-size:min(15cqh,11cqw);font-weight:800;line-height:1;
   padding:.12em .35em;border-radius:.3em;background:#0b0d10cc;font-variant-numeric:tabular-nums;
   animation:delta-fade var(--ms) linear forwards;animation-delay:var(--age)}
 .delta.loss{color:var(--lethal)}.delta.gain{color:#3dd68c}
 .delta small{font-size:.45em;font-weight:700;color:var(--dim);margin-left:.25em}
 @keyframes delta-fade{0%,75%{opacity:1}100%{opacity:0}}
 .name{font-size:min(13cqh,11cqw);font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
 /* Deck pips sit in the top-right corner, a little above the name. fitNames() shortens a name only if it reaches them. */
 .tile > .pips{position:absolute;top:2.2cqh;right:3cqw;font-size:min(14cqh,11.5cqw,6.5vh)}
 .tile.out > .pips{opacity:.45}
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
 .removes{display:flex;flex-wrap:wrap;gap:1.2vh;margin:0 0 1.6vh}
 .removes .btn{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
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
  <div class="row"><span class="lbl">Remove a player (undo brings them back)</span></div>
  <div class="removes" id="removes"></div>
  <div class="row"><span class="lbl">Full screen</span><button class="btn" id="fs">Full screen</button>
    <button class="btn" id="close">Close</button></div>
</dialog>
<div class="toast" id="toast" hidden></div>

<script>
"use strict";
const CODE = "__CODE__";
const POISON_LETHAL = __POISON_LETHAL__, CMD_LETHAL = __CMD_LETHAL__;
const COLORS = __COLORS__;
__PIP_JS__const $ = id => document.getElementById(id);
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
    ${pips(p.colors)}
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
  if (!("delta" in a)) { delta = null; clearTimeout(deltaTimer); return; }  // e.g. a removal
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
  // Players who left keep their name on commander damage they dealt.
  const names = {...state.departed, ...Object.fromEntries(players.map(p => [p.id, p.name]))};
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
  fitNames();
  $("join").innerHTML = players.length ? `Join at ${joinHtml()}` : "";
  $("hqr").hidden = !players.length;  // the empty screen shows a big one instead
  $("edits").setAttribute("aria-pressed", state.table_edits);
  $("edits").textContent = state.table_edits ? "On" : "Off";
  $("startLife").textContent = state.starting_life;
  renderRemoves();
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
// Two taps to remove: the first arms the button for 3 s.
let removeArmed = null, removeTimer = null;
function renderRemoves() {
  $("removes").innerHTML = state.players.length ? state.players.map(p => {
    const armed = removeArmed === p.id;
    return `<button class="btn ${armed ? "danger" : ""}" data-remove="${esc(p.id)}">${armed ? "Tap again to remove " : ""}${esc(p.name)}</button>`;
  }).join("") : `<span class="lbl">No players yet</span>`;
}
$("removes").onclick = e => {
  const b = e.target.closest("[data-remove]");
  if (!b) return;
  clearTimeout(removeTimer);
  if (removeArmed === b.dataset.remove) {
    removeArmed = null;
    act({type: "remove", target: b.dataset.remove});
  } else {
    removeArmed = b.dataset.remove;
    removeTimer = setTimeout(() => { removeArmed = null; renderRemoves(); }, 3000);
  }
  renderRemoves();
};

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


// Names are centered under the tile's top edge; deck pips sit in the top-right corner. If a name
// runs into the pips, shorten it (symmetrically, so it stays centered) to end just before them,
// unless that would leave under ~half the tile: then move the name down to just below the pips.
function fitNames() {
  for (const t of document.querySelectorAll(".tile")) {
    const name = t.querySelector(".name"), pipsEl = t.querySelector(":scope > .pips");
    name.style.maxWidth = name.style.marginTop = "";
    if (!pipsEl) continue;
    const nr = name.getBoundingClientRect(), pr = pipsEl.getBoundingClientRect(), tr = t.getBoundingClientRect();
    const gap = pr.height * 0.4;
    if (pr.bottom <= nr.top || nr.right <= pr.left - gap) continue;  // already clear
    const room = 2 * (pr.left - gap - (tr.left + tr.width / 2));
    if (room >= tr.width * 0.45) name.style.maxWidth = room + "px";
    else name.style.marginTop = (pr.bottom - nr.top + gap * 0.5) + "px";
  }
}
addEventListener("resize", fitNames);

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
<title>Room __CODE__ · EDH Table</title>
<style>
__PIP_CSS__ :root{--bg:#0b0d10;--card:#171a21;--btn:#262b36;--fg:#f4f5f7;--dim:#9aa1ad;--warn:#ffb224;--lethal:#ff4d4f;--c:#343a48}
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
   display:flex;align-items:center;gap:8px;white-space:nowrap}
 .me .nm{min-width:0;overflow:hidden;text-overflow:ellipsis}
 .fs{width:48px;height:48px;flex-shrink:0;margin-right:-10px;padding:0;background:none;color:var(--dim);
   display:flex;align-items:center;justify-content:center}
 .picker{display:grid;grid-template-columns:repeat(6,minmax(48px,56px));gap:6px}
 .picker .pip{width:100%;height:auto;aspect-ratio:1;font-size:20px;opacity:.3;padding:0}
 .picker .pip[aria-pressed="true"]{opacity:1;outline:3px solid var(--fg);outline-offset:2px}
 dialog{width:calc(100% - 32px);max-width:480px;background:#1b1f27;color:var(--fg);border:0;border-radius:16px;padding:20px}
 dialog::backdrop{background:#000a}
 dialog h2{margin:0;font-size:24px}
 .fld{display:flex;flex-direction:column;gap:10px;margin:18px 0;font-size:16px;color:var(--dim);font-weight:600}
 .fld input{height:56px;border-radius:12px;border:2px solid #343a48;background:var(--card);padding:0 14px;font-size:22px;color:var(--fg)}
 .actions{display:flex;gap:12px;margin-top:22px}
 .actions button{flex:1;height:56px;font-size:20px}
 .actions .primary{background:#3e63dd}
 .remove{width:100%;min-height:56px;background:none;border:2px solid #5a2a2d;color:#ff8a8c;font-size:18px;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 12px}
 .remove[data-armed]{background:#a4282b;border-color:#a4282b;color:#fff}
 dialog .remove{margin-top:18px}
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
 .join > button{height:64px;font-size:24px;background:#3e63dd}
 .msg{flex:1;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px;font-size:22px;color:var(--dim)}

 .toast{position:fixed;left:16px;right:16px;bottom:84px;background:#3a2a10;border:1px solid var(--warn);
   padding:14px 16px;border-radius:12px;font-size:17px;text-align:center}
</style></head>
<body>
<header>
  <button class="me" id="me" aria-label="Edit name and deck colors">EDH Table</button>
  <span class="status" id="status" hidden>reconnecting…</span>
  <span class="room">__CODE__</span>
  <button class="fs" id="fs" aria-label="Full screen" aria-pressed="false" hidden></button>
</header>

<div class="msg" id="loading">Connecting…</div>

<form class="join" id="join" hidden>
  <h1>Join room __CODE__</h1>
  <p id="joinNote">Your name shows on the table display.</p>
  <input id="name" maxlength="24" autocomplete="nickname" placeholder="Your name" enterkeyhint="go">
  <p>Deck colors (optional)</p>
  <div class="picker" id="joinColors" role="group" aria-label="Deck colors"></div>
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
  <button class="remove" id="removeOther" hidden></button>
</main>

<footer id="footer" hidden><button class="undo" id="undo" disabled>Nothing to undo</button></footer>
<dialog id="edit">
  <form id="editForm">
    <h2>Name and deck</h2>
    <label class="fld">Name<input id="editName" maxlength="24" autocomplete="nickname"></label>
    <div class="fld">Deck colors<div class="picker" id="editColors" role="group" aria-label="Deck colors"></div></div>
    <div class="actions"><button type="button" id="editCancel">Cancel</button><button type="submit" class="primary">Save</button></div>
    <button type="button" class="remove" id="leave">Leave this game</button>
  </form>
</dialog>
<div class="toast" id="toast" hidden></div>

<script>
"use strict";
const CODE = "__CODE__";
const POISON_LETHAL = __POISON_LETHAL__, CMD_LETHAL = __CMD_LETHAL__;
const COLORS = __COLORS__;
__PIP_JS__const TOKEN_KEY = "mtg-token-" + CODE;
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
    $("me").textContent = "EDH Table";
    $("joinNote").textContent = me ? "You were removed from this game. Join again to get a new seat."
                                   : "Your name shows on the table display.";
    if ($("join").hidden) { show("join"); }
    return;
  }
  show("game");
  keepAwake();
  $("me").innerHTML = `<span class="nm">${esc(p.name)}</span>${pips(p.colors)}<small>✎</small>`;
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
  $("removeOther").hidden = mine;
  if (!$("removeOther").dataset.armed) $("removeOther").textContent = `Remove ${v.name} from the game`;

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
  const rm = $("removeOther");  // disarm a pending "remove" meant for the previous player
  if (rm.dataset.armed) { clearTimeout(Number(rm.dataset.armed)); delete rm.dataset.armed; }
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
  if (a.type === "remove") return `removed ${a.name}`;
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
  act({type: "join", name, colors: pickerValue($("joinColors"))});
};
$("undo").onclick = () => act({type: "undo"});
// ---- name and deck colors ------------------------------------------------------------

function makePicker(el) {
  el.innerHTML = [..."WUBRGC"].map(c =>
    `<button type="button" class="pip pip-${c}" data-c="${c}" aria-pressed="false" aria-label="${COLOR_NAMES[c]}">${c}</button>`).join("");
  el.addEventListener("click", e => {
    const b = e.target.closest("[data-c]");
    if (!b) return;
    const on = b.getAttribute("aria-pressed") !== "true";
    // Colorless can't be combined with colors: picking one side clears the other.
    if (on) for (const o of el.querySelectorAll("[data-c]")) {
      if ((o.dataset.c === "C") !== (b.dataset.c === "C")) o.setAttribute("aria-pressed", "false");
    }
    b.setAttribute("aria-pressed", String(on));
  });
}
const pickerValue = el => [...el.querySelectorAll('[aria-pressed="true"]')].map(b => b.dataset.c).join("");
function setPicker(el, colors) {
  for (const b of el.querySelectorAll("[data-c]")) b.setAttribute("aria-pressed", String(colors.includes(b.dataset.c)));
}
makePicker($("joinColors"));
makePicker($("editColors"));

const myPlayer = () => me && state && state.players.find(x => x.id === me);
$("me").onclick = () => {
  const p = myPlayer();
  if (!p) return;
  $("editName").value = p.name;
  setPicker($("editColors"), p.colors);
  $("edit").showModal();
};
$("editCancel").onclick = () => $("edit").close();

// Removing a player takes two taps: the first arms the button for 3 s.
function twoTap(btn, armedText, run) {
  if (btn.dataset.armed) {
    clearTimeout(Number(btn.dataset.armed));
    delete btn.dataset.armed;
    run();
    return;
  }
  btn.dataset.label = btn.textContent;
  btn.textContent = armedText;
  btn.dataset.armed = setTimeout(() => { delete btn.dataset.armed; btn.textContent = btn.dataset.label; }, 3000);
}
$("removeOther").onclick = () => {
  const target = viewId(), v = state.players.find(x => x.id === target);
  twoTap($("removeOther"), `Tap again to remove ${v.name}`, () => {
    viewing = null;
    act({type: "remove", target});
    render();  // back to your own totals straight away
  });
};
$("leave").onclick = () => twoTap($("leave"), "Tap again to leave", () => {
  $("edit").close();
  act({type: "remove"});
});
$("edit").addEventListener("close", () => {
  const b = $("leave");
  if (b.dataset.armed) { clearTimeout(Number(b.dataset.armed)); delete b.dataset.armed; }
  b.textContent = "Leave this game";
});
$("editForm").onsubmit = e => {
  e.preventDefault();
  const p = myPlayer();
  if (p) {
    const name = $("editName").value.trim(), colors = pickerValue($("editColors"));
    if (name && name !== p.name) act({type: "rename", name});
    if (colors !== p.colors) act({type: "colors", colors});
  }
  $("edit").close();
};

// ---- screen: stay awake during the game, optional full screen ---------------------------------

// Phones dim and lock after a short idle time; a wake lock keeps the screen on while this page is
// visible. Browsers drop it when the page is hidden, so it's requested again on return and on taps.
let wakeLock = null, wakePending = false;
async function keepAwake() {
  if (!("wakeLock" in navigator) || document.visibilityState !== "visible") return;
  if (wakePending || (wakeLock && !wakeLock.released)) return;
  wakePending = true;
  try { wakeLock = await navigator.wakeLock.request("screen"); } catch {} finally { wakePending = false; }
}
document.addEventListener("visibilitychange", () => { if (me) keepAwake(); });
document.addEventListener("pointerdown", () => { if (me) keepAwake(); });

// Full screen works on Android and iPad; iPhones only allow it for video, so the button stays hidden there.
const FS_ENTER = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
const FS_EXIT = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>';
const root = document.documentElement;
const inFullscreen = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
function syncFullscreenButton() {
  const on = inFullscreen();
  $("fs").innerHTML = on ? FS_EXIT : FS_ENTER;
  $("fs").setAttribute("aria-pressed", String(on));
  $("fs").setAttribute("aria-label", on ? "Exit full screen" : "Full screen");
}
$("fs").hidden = !(document.fullscreenEnabled || document.webkitFullscreenEnabled);
$("fs").onclick = () => {
  if (inFullscreen()) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    return;
  }
  const request = root.requestFullscreen || root.webkitRequestFullscreen;
  Promise.resolve(request.call(root)).catch(() => toast("Full screen isn't available here"));
};
for (const ev of ["fullscreenchange", "webkitfullscreenchange"]) document.addEventListener(ev, syncFullscreenButton);
syncFullscreenButton();

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
        page.replace("__PIP_CSS__", PIP_CSS)
        .replace("__PIP_JS__", PIP_JS)
        .replace("__CODE__", code)
        .replace("__POISON_LETHAL__", str(POISON_LETHAL))
        .replace("__CMD_LETHAL__", str(COMMANDER_DAMAGE_LETHAL))
        .replace("__COLORS__", json.dumps(PLAYER_COLORS))
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_PAGE.replace("__PIP_CSS__", PIP_CSS)


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

