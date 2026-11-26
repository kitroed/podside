"""FastAPI app: HTTP routes, the /ws/{code} endpoint, broadcasting, and serving the pages in static/."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

import qr

from actions import handle_action
from models import COMMANDER_DAMAGE_LETHAL, DEFAULT_STARTING_LIFE, POISON_LETHAL, ActionError, Player, Room
from rooms import manager

log = logging.getLogger("podside")
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


app = FastAPI(title="Podside", lifespan=lifespan)


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
# Pages: plain files in static/, served by app.frontend() below
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"

# One color per seat, shared by the display and player views.
PLAYER_COLORS = ["#e5484d", "#3e9bff", "#30a46c", "#ffb224", "#a970ff", "#12b5b0", "#ff6bb5", "#c2c7d0"]


@app.middleware("http")
async def revalidate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    # Make browsers check for new files after a deploy instead of reusing a stale copy.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/config.js")
async def config_js() -> Response:
    """Rule numbers and seat colors for the pages, so they're defined only in Python."""
    js = (
        f"const POISON_LETHAL = {POISON_LETHAL}, CMD_LETHAL = {COMMANDER_DAMAGE_LETHAL};\n"
        f"const COLORS = {json.dumps(PLAYER_COLORS)};\n"
    )
    return Response(js, media_type="text/javascript")


def _room_code(code: str) -> str:
    room = manager.get(code)
    if room is None:
        raise HTTPException(404, "Room not found")
    return room.code


@app.get("/display/{code}")
async def display_page(code: str) -> FileResponse:
    _room_code(code)
    return FileResponse(STATIC_DIR / "display.html")


@app.get("/play/{code}")
async def play_page(code: str) -> FileResponse:
    _room_code(code)
    return FileResponse(STATIC_DIR / "play.html")


@app.get("/api/rooms/{code}/qr.svg")
async def room_qr(
    code: str,
    base: str = Query(max_length=200, pattern=r"^https?://[A-Za-z0-9.\-\[\]:]+$"),
) -> Response:
    """QR code for {base}/play/{code}. The display passes its own origin as base."""
    svg = qr.svg(f"{base}/play/{_room_code(code)}")
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


# Low priority: the routes above are matched first.
app.frontend("/", directory=STATIC_DIR, fallback=None)
