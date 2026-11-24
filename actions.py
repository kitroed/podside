"""handle_action(): validates each client action and commits it to the room."""

from __future__ import annotations

from typing import Any

from models import MAX_DELTA, ActionError, Player, Room, clean_name


def as_delta(value: Any) -> int:
    try:
        d = int(value)
    except (TypeError, ValueError):
        raise ActionError("delta must be an integer")
    if d == 0 or abs(d) > MAX_DELTA:
        raise ActionError(f"delta must be non-zero and within ±{MAX_DELTA}")
    return d


def handle_action(room: Room, actor: Player | None, msg: dict[str, Any]) -> None:
    """Validate and apply one action. Caller holds room.lock and broadcasts afterward."""
    kind = msg.get("type")
    actor_id = actor.id if actor else "display"

    def target_player() -> Player:
        pid = msg.get("target") or (actor.id if actor else None)
        p = room.players.get(pid) if pid else None
        if p is None:
            raise ActionError("Unknown target player")
        # Permission: players edit only themselves unless table edits are on.
        # The display can always edit (it's the "table" device).
        if actor and p.id != actor.id and not room.table_edits:
            raise ActionError("You can only change your own totals (table edits are off)")
        return p

    if kind == "life":
        p = target_player()
        room.commit(actor_id, msg, [(p.id, "life", None, as_delta(msg.get("delta")))])

    elif kind == "poison":
        p = target_player()
        delta = as_delta(msg.get("delta"))
        delta = max(delta, -p.poison)  # don't go below zero; keep undo exact
        if delta == 0:
            return
        room.commit(actor_id, msg, [(p.id, "poison", None, delta)])

    elif kind == "cmd_damage":
        p = target_player()
        source = msg.get("source")
        if source not in room.players or source == p.id:
            raise ActionError("Invalid commander damage source")
        delta = as_delta(msg.get("delta"))
        delta = max(delta, -p.cmd_damage.get(source, 0))
        if delta == 0:
            return
        # Commander damage is also life loss; record both so undo reverts both.
        room.commit(actor_id, msg, [(p.id, "cmd_damage", source, delta), (p.id, "life", None, -delta)])

    elif kind == "counter":
        p = target_player()
        name = clean_name(msg.get("name")).lower()
        if not name:
            raise ActionError("Counter name required")
        room.commit(actor_id, msg, [(p.id, "counters", name, as_delta(msg.get("delta")))])

    elif kind == "rename":
        p = target_player()
        new_name = clean_name(msg.get("name"))
        if not new_name:
            raise ActionError("Name required")
        p.name = new_name
        room.seq += 1  # not undoable, but bump seq so clients refresh

    elif kind == "undo":
        room.undo()

    elif kind == "reset":
        room.reset_game()

    elif kind == "settings":
        if "starting_life" in msg:
            try:
                life = int(msg["starting_life"])
            except (TypeError, ValueError):
                raise ActionError("starting_life must be a whole number")
            if not 1 <= life <= 999:
                raise ActionError("starting_life out of range")
            room.starting_life = life
        if "table_edits" in msg:
            room.table_edits = bool(msg["table_edits"])
        room.seq += 1

    else:
        raise ActionError(f"Unknown action type: {kind!r}")
