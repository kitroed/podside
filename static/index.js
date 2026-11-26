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
