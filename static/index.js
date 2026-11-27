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

// Resume: upload a file saved from a game. It becomes a new room, and this screen its scoreboard.
$("resume").onclick = () => $("resumeFile").click();
$("resumeFile").onchange = async () => {
  const file = $("resumeFile").files[0];
  $("resumeFile").value = "";  // so picking the same file again still fires
  if (!file) return;
  $("startErr").textContent = "";
  try {
    const r = await fetch("/api/rooms/resume", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: await file.text(),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      $("startErr").textContent = body.detail || "Couldn't resume that game.";
      return;
    }
    location.href = body.display_url;
  } catch {
    $("startErr").textContent = "Couldn't reach the server. Try again.";
  }
};

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
    if (r.status === 404) {
      $("joinErr").textContent = `No game with code ${code}. Check the TV.`;
      return;
    }
    if (!r.ok) throw new Error(r.status);
    location.href = `/play/${code}`;
  } catch {
    $("joinErr").textContent = "Couldn't reach the server. Try again.";
  }
};
