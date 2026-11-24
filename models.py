"""Config constants and the domain model: Player, Change, LogEntry, Room."""

from __future__ import annotations

import asyncio
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_PLAYERS = 8
DEFAULT_STARTING_LIFE = 40
ROOM_IDLE_TTL_SECONDS = 6 * 60 * 60  # drop rooms nobody has touched in 6h
CLEANUP_INTERVAL_SECONDS = 10 * 60
UNDO_DEPTH = 200
MAX_DELTA = 1000  # sanity cap on a single action
ROOM_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase if c not in "IO")  # no I/O confusion
ROOM_CODE_LENGTH = 4
COMMANDER_DAMAGE_LETHAL = 21
POISON_LETHAL = 10


class ActionError(Exception):
    """Raised for invalid client actions; message goes back to that client."""


def clean_name(name: Any) -> str:
    return str(name or "").strip()[:24]


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclass
class Player:
    id: str
    name: str
    token: str
    life: int
    poison: int = 0
    cmd_damage: dict[str, int] = field(default_factory=dict)  # source player id -> damage taken
    counters: dict[str, int] = field(default_factory=dict)  # energy, experience, etc.

    @property
    def eliminated(self) -> bool:
        return (
            self.life <= 0
            or self.poison >= POISON_LETHAL
            or any(d >= COMMANDER_DAMAGE_LETHAL for d in self.cmd_damage.values())
        )

    def public(self) -> dict[str, Any]:
        # Never send tokens to other clients
        return {
            "id": self.id,
            "name": self.name,
            "life": self.life,
            "poison": self.poison,
            "cmd_damage": self.cmd_damage,
            "counters": self.counters,
            "eliminated": self.eliminated,
        }


# A single reversible change: (player_id, field, key, delta).
# key is None for scalar fields (life, poison), or a dict key for cmd_damage/counters.
Change = tuple[str, str, str | None, int]


@dataclass
class LogEntry:
    seq: int
    ts: float
    actor: str  # player id or "display"
    action: dict[str, Any]
    changes: list[Change]


@dataclass
class Room:
    code: str
    starting_life: int = DEFAULT_STARTING_LIFE
    table_edits: bool = True  # anyone can edit anyone; False limits players to themselves
    players: dict[str, Player] = field(default_factory=dict)
    seq: int = 0
    history: list[LogEntry] = field(default_factory=list)
    connections: set[WebSocket] = field(default_factory=set)
    last_activity: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _next_player_num: int = 1

    # ---- players --------------------------------------------------------

    def add_player(self, name: str) -> Player:
        if len(self.players) >= MAX_PLAYERS:
            raise ActionError(f"Room is full ({MAX_PLAYERS} players max)")
        pid = f"p{self._next_player_num}"
        self._next_player_num += 1
        player = Player(id=pid, name=clean_name(name) or pid.upper(), token=secrets.token_urlsafe(16), life=self.starting_life)
        self.players[pid] = player
        return player

    def player_by_token(self, token: str) -> Player | None:
        return next((p for p in self.players.values() if secrets.compare_digest(p.token, token)), None)

    # ---- state ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "seq": self.seq,
            "starting_life": self.starting_life,
            "table_edits": self.table_edits,
            "players": [p.public() for p in self.players.values()],
            "last_action": self._describe(self.history[-1]) if self.history else None,
        }

    def _describe(self, entry: LogEntry) -> dict[str, Any]:
        return {"seq": entry.seq, "actor": entry.actor, "action": entry.action, "ts": entry.ts}

    # ---- applying changes ---------------------------------------------------

    def _apply_change(self, change: Change, sign: int = 1) -> None:
        pid, fld, key, delta = change
        p = self.players.get(pid)
        if p is None:
            return
        d = delta * sign
        if fld == "life":
            p.life += d
        elif fld == "poison":
            p.poison = max(0, p.poison + d)
        elif fld in ("cmd_damage", "counters"):
            bucket: dict[str, int] = getattr(p, fld)
            new_val = bucket.get(key, 0) + d
            if new_val:
                bucket[key] = new_val
            else:
                bucket.pop(key, None)

    def commit(self, actor: str, action: dict[str, Any], changes: list[Change]) -> None:
        for c in changes:
            self._apply_change(c)
        self.seq += 1
        self.history.append(LogEntry(self.seq, time.time(), actor, action, changes))
        if len(self.history) > UNDO_DEPTH:
            self.history.pop(0)

    def undo(self) -> LogEntry:
        # Skip entries with no reversible changes (e.g. renames)
        while self.history:
            entry = self.history.pop()
            if entry.changes:
                for c in reversed(entry.changes):
                    self._apply_change(c, sign=-1)
                self.seq += 1
                return entry
        raise ActionError("Nothing to undo")

    def reset_game(self) -> None:
        for p in self.players.values():
            p.life = self.starting_life
            p.poison = 0
            p.cmd_damage.clear()
            p.counters.clear()
        self.history.clear()
        self.seq += 1
