# MTG Life Tracker

A multiplayer Magic: The Gathering life tracker for in-person play. A shared **table display** (TV, monitor or tablet in a browser) shows every player's totals, and each player controls their own totals from **their phone**, the way Jackbox works.

Existing apps (Playgroup.gg, Sideboard, Signet, Moxtopper) either run on one shared device or sync phone to phone. None of them have a real big-screen view that is separate from the phones. That gap is what this project is for.

## Status

Early but usable. The server works (split into modules, see Code layout) and has a pytest suite. The display view and the player view are both built.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install "fastapi[standard]"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://<host>:8000/` and click **Create room**. Open the display link on the TV and the player link on each phone.

## Deploying

The app runs as one Docker container (see `Dockerfile` and `docker-compose.yml`), behind a reverse proxy for HTTPS.

1. Copy `deploy.sh.example` to `deploy.sh`, set `HOST` and `REMOTE_DIR`, and run `sh deploy.sh`. It rsyncs the source to the server and runs `docker compose up -d --build` there. A redeploy ends any games in progress, because rooms live in memory.
2. In Nginx Proxy Manager, add a proxy host for the subdomain: scheme `http`, forward to the Docker host's IP on port `8086` (the host port in `docker-compose.yml`), and turn on **Websockets Support**. On the SSL tab, request a Let's Encrypt certificate and turn on **Force SSL**.

Keep it to one worker (`--workers 1`, already set in the Dockerfile). Rooms live in that one process's memory.

HTTPS matters for more than security: browsers only let the display keep the screen awake on HTTPS pages (or on `localhost`).

## Architecture

```
 Table display (TV)        Player phones
   read + admin              own totals
        \                      /
         \     WebSocket      /
          +---- Room server -+      FastAPI, single process
                   |
              Room state            in-memory (SQLite later)
```

- **The server is the only source of truth.** Clients never set values directly. They send *actions*, and the server validates them, applies them under a per-room `asyncio.Lock`, adds them to the undo log, and sends the full room state to every connection.
- **Actions, not values.** If two people tap at the same moment, both changes count. Each action is saved as a list of reversible `Change` tuples `(player_id, field, key, delta)`, so undo just reverses them in order.
- **Full state on every update.** State is small (8 players max), so the server sends a complete snapshot after each change and on connect. That keeps reconnects simple. Every snapshot carries a `seq` number.
- **Reconnect by token.** Joining gives the player a token, which the client keeps in `localStorage`. Connecting with `?token=` gives the player their seat back.

### Rules built in

| Rule | Value |
|---|---|
| Default starting life | 40 |
| Max players | 8 |
| Poison lethal | 10 |
| Commander damage lethal | 21 from a single source |
| Commander damage | also lowers life, and undo reverses both |
| Permissions | `table_edits` is on by default, so anyone can change anyone's totals. Turn it off to limit players to their own; the display can always change anything |

## HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/rooms?starting_life=40` | Create a room. Returns `code`, `display_url`, `play_url` |
| `GET` | `/api/rooms/{code}` | Current room snapshot |
| `GET` | `/` | Landing page with a "Create room" button |
| `GET` | `/display/{code}` | Display view for the TV |
| `GET` | `/play/{code}` | Player view for phones |

## WebSocket protocol

Connect to `/ws/{code}?role=display` or `/ws/{code}?role=player[&token=...]`. All messages are JSON.

**Client → server**

```jsonc
{"type": "join", "name": "Kit"}                                // player connections only
{"type": "life", "target": "p1", "delta": -3}
{"type": "poison", "target": "p1", "delta": 1}
{"type": "cmd_damage", "target": "p1", "source": "p2", "delta": 5}
{"type": "counter", "target": "p1", "name": "energy", "delta": 1}
{"type": "rename", "target": "p1", "name": "Kit"}
{"type": "undo"}
{"type": "reset"}                                              // new game, same players
{"type": "settings", "starting_life": 40, "table_edits": true}
{"type": "ping"}
```

`target` defaults to the sender's own player when left out.

**Server → client**

```jsonc
{"type": "welcome", "player_id": "p1", "token": "..."}
{"type": "state", "state": {
  "code": "WEVK", "seq": 12, "starting_life": 40, "table_edits": true,
  "players": [{"id": "p1", "name": "Kit", "life": 37, "poison": 0,
               "cmd_damage": {"p2": 3}, "counters": {}, "eliminated": false}],
  "last_action": {"seq": 12, "actor": "p1", "action": {...}, "ts": 1732327748.6}
}}
{"type": "error", "message": "..."}
{"type": "pong"}
```

## Code layout

| File | What's there |
|---|---|
| `models.py` | Config constants, `ActionError`, and the domain model: `Player`, `Change`, `LogEntry`, `Room` (state, apply/commit/undo/reset) |
| `rooms.py` | `RoomManager` and the shared `manager`: create/get rooms, idle cleanup |
| `actions.py` | `handle_action()`: validates and applies each action type |
| `app.py` | FastAPI routes, the `/ws/{code}` endpoint, `broadcast_state()`, and the inline HTML pages: the landing page, the display view and the player view |
| `main.py` | Entry point that re-exports `app`, so `uvicorn main:app` works |
| `tests/` | pytest suite. `conftest.py` has a `Table` helper that drives a display and several players over real WebSockets |

Run the tests with `pip install pytest && pytest -q`.

## Roadmap

**Next up**
- [x] Split `main.py` into modules: `models.py`, `rooms.py`, `actions.py`, `app.py`
- [x] Turn the ad-hoc test script into a `pytest` suite (multi-client, permissions, undo, reconnect)
- [x] Real **display view**: large tiles, commander damage on each tile (colored by source), eliminated-player styling, the join link, and a Wake Lock so the TV doesn't sleep
- [ ] QR code to join on the display (needs a QR library or a hand-rolled encoder)
- [x] Real **player view**: big +/- buttons, hold to repeat, a commander damage picker, undo
- [ ] Batch rapid taps for about 400 ms so the display shows "−5" instead of five −1s
- [ ] Static files in `static/` and templates in place of the inline HTML

**Later**
- [ ] SQLite persistence for rooms and the action log, so games survive restarts
- [ ] Game history and stats (wins, decks, commanders) built from the action log
- [ ] Turn tracking and an optional turn timer
- [ ] Monarch, initiative, day/night, and other table-wide markers
- [ ] Partner commanders (two damage sources per player)
- [ ] Player kick and seat reordering from the display
- [x] Dockerfile for the home server; a reverse proxy needs WebSocket upgrade headers

## Constraints and conventions

- Python 3.11+, FastAPI, with no build step for the front end (vanilla JS, or htmx/Alpine if needed)
- Single process, in-memory state. Don't add Redis unless it's ever run as more than one instance
- Every state change goes through `handle_action()` → `Room.commit()` so undo and history stay correct
- Never send player tokens in broadcast state (`Player.public()` leaves them out)
