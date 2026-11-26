# AGENTS.md

Instructions for coding agents working in this repo. Read `README.md` for the project overview, the WebSocket protocol and the roadmap. This file covers how to work here.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install "fastapi[standard]" pytest
uvicorn main:app --reload --port 8000     # run locally
pytest -q                                 # run tests
ruff check . && ruff format .             # lint and format (if ruff is installed)
```

Run `pytest` before saying a task is done. If there are no tests yet, add them for whatever you changed.

## Stack

- Python 3.11+ and FastAPI, run with uvicorn. One process with in-memory state.
- Front end: plain HTML, CSS and vanilla JS served by FastAPI. **No build step, bundler or npm.**
- Don't add new dependencies (Redis, an ORM, a front-end framework) without asking first.

## Architecture rules (don't break these)

1. **The server is the only source of truth.** Clients send actions; they never send state or set values directly.
2. **Every state change goes through `handle_action()` → `Room.commit()`** as a list of reversible `Change` tuples. That's what keeps undo and history correct. Never change `Player` fields directly anywhere else.
3. **Undo has to be exact.** If a change is clamped (like poison not going below zero), clamp the delta *before* recording it so the reverse change is correct.
4. **Broadcast the full snapshot** after every accepted action. Don't add partial or diff updates unless asked.
5. **Never send player tokens to clients** except to their own owner in a `welcome` message. `Player.public()` is the only thing that goes into broadcast state.
6. **Permissions:** players change only themselves unless `table_edits` is on. The display connection can change anything.
7. Changes to the protocol (new action types or new state fields) **must be updated in the README's protocol section in the same change.**

## Testing

- Use `fastapi.testclient.TestClient` with `websocket_connect` to simulate the display and several players in one test.
- Every new action type needs tests for: the normal case, a permission refusal, and undo reversing it.
- Cover reconnecting with a token whenever you change how players join.

## Code style

- Type hints everywhere, dataclasses for the domain model, small functions.
- Raise `ActionError` with a message a player can read for bad client input. It goes back to that client only.
- Use logging, not print statements.
- Keep comments short, and only where the reason isn't obvious from the code.

## UI guidelines

- **Display view** (TV, read across a room): huge life totals, high contrast, dark background, no small text. It has to work at 1080p from about 10 feet away.
- **Player view** (phone, used one-handed): large tap targets (at least 48px), hold to repeat on +/-, and nothing that needs precise taps mid-game.
- Both views reconnect automatically and show a small "reconnecting…" indicator.

## Working agreements

- Keep changes small and focused. If you're about to change more than one roadmap item at once, stop and check in first.
- When you finish a roadmap item, tick it in `README.md`.
- Ask before deleting code or making big structural changes you weren't asked for.
