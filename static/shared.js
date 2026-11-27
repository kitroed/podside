"use strict";
// Helpers shared by the display and player pages.

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
