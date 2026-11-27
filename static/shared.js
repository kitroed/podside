"use strict";
// Helpers shared by the display and player pages. Loaded after config.js (COLORS)
// and before the page's own script.

// The server only serves this page for a room that exists.
const CODE = decodeURIComponent(location.pathname.split("/").pop()).toUpperCase();
const $ = id => document.getElementById(id);

const ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
const esc = s => String(s).replace(/[&<>"']/g, c => ESCAPES[c]);
// Each seat's color, from its id: p1 gets COLORS[0], and so on.
const colorOf = pid => COLORS[(parseInt(pid.slice(1), 10) - 1) % COLORS.length];
// Signed amount with a real minus sign, e.g. "+3" or "−5".
const signed = n => (n > 0 ? "+" : "−") + Math.abs(n);

// Point a download link at this room's save file, named with the local date and time.
function saveLink(a) {
  const t = new Date(), pad = n => String(n).padStart(2, "0");
  const date = `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
  const stamp = `${date}-${pad(t.getHours())}${pad(t.getMinutes())}`;
  a.href = `/api/rooms/${CODE}/save`;
  a.download = `podside-${CODE}-${stamp}.json`;
}

const COLOR_NAMES = {W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless"};
// Deck color pips, e.g. pips("WU"). Screen readers hear the color names once, not each letter.
function pips(colors) {
  if (!colors) return "";
  const label = [...colors].map(x => COLOR_NAMES[x]).join(", ");
  const dots = [...colors].map(x => `<span class="pip pip-${x}" aria-hidden="true">${x}</span>`).join("");
  return `<span class="pips" role="img" aria-label="${label}">${dots}</span>`;
}
