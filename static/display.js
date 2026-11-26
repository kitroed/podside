"use strict";
// The server only serves this page for a room that exists.
const CODE = decodeURIComponent(location.pathname.split("/").pop()).toUpperCase();
const $ = id => document.getElementById(id);
document.title = `Room ${CODE} · Podside`;
$("roomCode").textContent = CODE;
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
  return `<section class="tile ${out ? "out" : ""} ${p.claimed ? "" : "unclaimed"}" data-pid="${esc(p.id)}" style="--c:${colorOf(p.id)}">
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
  renderSeats();
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
saveLink($("save"));
$("save").onclick = () => saveLink($("save"));  // refresh the time in the file name
$("edits").onclick = () => act({type: "settings", table_edits: !state.table_edits});
for (const b of document.querySelectorAll("[data-life]")) {
  b.onclick = () => {
    // Step through common formats rather than one point at a time.
    const cur = state.starting_life, up = b.dataset.life === "1";
    const next = up ? STARTING_LIFE_PRESETS.find(v => v > cur) : [...STARTING_LIFE_PRESETS].reverse().find(v => v < cur);
    if (next) act({type: "settings", starting_life: next});
  };
}
// Players: ▲▼ match the TV to where people sit; Remove takes two taps (the first arms it for 3 s).
let removeArmed = null, removeTimer = null;
function renderSeats() {
  const ps = state.players;
  $("seats").innerHTML = ps.length ? ps.map((p, i) => {
    const armed = removeArmed === p.id, name = esc(p.name);
    return `<div class="seat" style="--c:${colorOf(p.id)}">
      <span class="nm"><span class="dot"></span><span>${name}</span></span>
      <button class="btn" data-move="${p.id}" data-dir="-1" aria-label="Move ${name} earlier" ${i === 0 ? "disabled" : ""}>▲</button>
      <button class="btn" data-move="${p.id}" data-dir="1" aria-label="Move ${name} later" ${i === ps.length - 1 ? "disabled" : ""}>▼</button>
      <button class="btn ${armed ? "danger" : ""}" data-remove="${p.id}" aria-label="Remove ${name}">${armed ? "Tap again" : "Remove"}</button>
    </div>`;
  }).join("") : `<span class="lbl">No players yet</span>`;
}
$("seats").onclick = e => {
  const move = e.target.closest("[data-move]");
  if (move) {
    const order = state.players.map(p => p.id), i = order.indexOf(move.dataset.move), j = i + Number(move.dataset.dir);
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    act({type: "reorder", order});
    return;
  }
  const b = e.target.closest("[data-remove]");
  if (!b) return;
  clearTimeout(removeTimer);
  if (removeArmed === b.dataset.remove) {
    removeArmed = null;
    act({type: "remove", target: b.dataset.remove});
  } else {
    removeArmed = b.dataset.remove;
    removeTimer = setTimeout(() => { removeArmed = null; renderSeats(); }, 3000);
  }
  renderSeats();
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
