"use strict";
// The server only serves this page for a room that exists.
const CODE = decodeURIComponent(location.pathname.split("/").pop()).toUpperCase();
const TOKEN_KEY = "podside-token-" + CODE;
const HOLD_DELAY = 400, HOLD_EVERY = 120;  // ms before repeating, then between repeats

const $ = id => document.getElementById(id);
document.title = `Room ${CODE} · Podside`;
$("roomCode").textContent = CODE;
$("joinCode").textContent = CODE;
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
    $("me").textContent = "Podside";
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
