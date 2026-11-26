"use strict";
const COLOR_NAMES = {W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless"};
const pips = c => !c ? "" : `<span class="pips" role="img" aria-label="${[...c].map(x => COLOR_NAMES[x]).join(", ")}">`
  + [...c].map(x => `<span class="pip pip-${x}" aria-hidden="true">${x}</span>`).join("") + "</span>";
