"""Config constants and the domain model: Player, Change, LogEntry, Room."""

from __future__ import annotations

import asyncio
import secrets
import string
import time
from collections.abc import Callable
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
GROUP_WINDOW_SECONDS = 0.4  # repeats of the same action this close together share one history entry
MAX_DELTA = 1000  # sanity cap on a single action
ROOM_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase if c not in "IO")  # no I/O confusion
ROOM_CODE_LENGTH = 4
COMMANDER_DAMAGE_LETHAL = 21
POISON_LETHAL = 10


class ActionError(Exception):
    """Raised for invalid client actions; message goes back to that client."""


def clean_name(name: Any) -> str:
    return str(name or "").strip()[:24]


COLOR_ORDER = "WUBRG"  # white, blue, black, red, green; "C" is colorless


def clean_colors(value: Any) -> str:
    """Deck colors as letters in WUBRG order, "C" alone for colorless, or "" for unset."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ActionError("Colors must be a string of letters, like \"WUG\"")
    letters = set(value.upper().replace(" ", ""))
    if not letters <= set(COLOR_ORDER + "C"):
        raise ActionError("Colors must be letters from W, U, B, R, G, or C for colorless")
    if "C" in letters:
        if len(letters) > 1:
            raise ActionError("Colorless can't be combined with other colors")
        return "C"
    return "".join(c for c in COLOR_ORDER if c in letters)


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclass
class Player:
    id: str
    name: str
    token: str | None  # None for a seat restored from a save that nobody has claimed yet
    life: int
    poison: int = 0
    cmd_damage: dict[str, int] = field(default_factory=dict)  # source player id -> damage taken
    counters: dict[str, int] = field(default_factory=dict)  # energy, experience, etc.
    colors: str = ""  # deck colors, see clean_colors()

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
            "colors": self.colors,
            "claimed": self.token is not None,
        }


# A single reversible change: (player_id, field, key, delta).
# key is None for scalar fields (life, poison), or a dict key for cmd_damage/counters.
Change = tuple[str, str, str | None, int]


@dataclass
class Removal:
    """A removed player, kept whole so undo can put them back exactly."""

    player: Player
    seat: int  # position in the player order


@dataclass
class LogEntry:
    seq: int
    ts: float  # time of the latest action folded into this entry
    actor: str  # player id or "display"
    action: dict[str, Any]  # normalised action; "delta" is the group's total
    changes: list[Change]
    closed: bool = False  # set once an undo exposes this entry, so new taps start a fresh group
    removal: Removal | None = None  # set for "remove" actions, which aren't expressible as Changes

    def can_absorb(self, actor: str, action: dict[str, Any], now: float) -> bool:
        def key(a: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in a.items() if k != "delta"}

        return (
            not self.closed
            and self.actor == actor
            and "delta" in action
            and key(self.action) == key(action)
            and now - self.ts <= GROUP_WINDOW_SECONDS
        )

    def absorb(self, action: dict[str, Any], changes: list[Change], now: float) -> None:
        totals: dict[tuple[str, str, str | None], int] = {}
        for pid, fld, key, delta in [*self.changes, *changes]:
            totals[(pid, fld, key)] = totals.get((pid, fld, key), 0) + delta
        self.changes = [(pid, fld, key, d) for (pid, fld, key), d in totals.items()]
        self.action = {**self.action, "delta": self.action["delta"] + action["delta"]}
        self.ts = now

    @property
    def is_noop(self) -> bool:
        return all(d == 0 for *_, d in self.changes)


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
    clock: Callable[[], float] = field(default=time.time, repr=False)  # replaceable in tests
    departed: dict[str, str] = field(default_factory=dict)  # removed player id -> name, for their old cmd damage
    _next_player_num: int = 1

    # ---- players --------------------------------------------------------

    def add_player(self, name: str, colors: Any = None) -> Player:
        if len(self.players) >= MAX_PLAYERS:
            raise ActionError(f"Room is full ({MAX_PLAYERS} players max)")
        deck_colors = clean_colors(colors)  # validate before taking a seat number
        pid = f"p{self._next_player_num}"
        self._next_player_num += 1
        player = Player(
            id=pid,
            name=clean_name(name) or pid.upper(),
            token=secrets.token_urlsafe(16),
            life=self.starting_life,
            colors=deck_colors,
        )
        self.players[pid] = player
        return player

    def player_by_token(self, token: str) -> Player | None:
        return next((p for p in self.players.values() if p.token and secrets.compare_digest(p.token, token)), None)

    def claim_seat(self, pid: Any) -> Player:
        """Give an unclaimed seat (from a resumed save) to the phone that asks for it."""
        player = self.players.get(str(pid))
        if player is None:
            raise ActionError("That seat isn't in this game any more")
        if player.token is not None:
            raise ActionError(f"Someone has already taken {player.name}'s seat")
        player.token = secrets.token_urlsafe(16)
        self.seq += 1
        return player

    # ---- state ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "seq": self.seq,
            "starting_life": self.starting_life,
            "table_edits": self.table_edits,
            "players": [p.public() for p in self.players.values()],
            "last_action": self._describe(self.history[-1]) if self.history else None,
            "departed": self.departed_names(),
        }

    def departed_names(self) -> dict[str, str]:
        """Names of removed players whose commander damage is still on someone's totals."""
        sources = {src for p in self.players.values() for src in p.cmd_damage}
        return {pid: name for pid, name in self.departed.items() if pid in sources}

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
        now = self.clock()
        last = self.history[-1] if self.history else None
        # Rapid repeats (taps, or a held button) become one entry: one undo step, one running total.
        if last and last.can_absorb(actor, action, now):
            last.absorb(action, changes, now)
            if last.is_noop:  # e.g. +1 then -1: nothing left to undo
                self.history.pop()
            return
        self.history.append(LogEntry(self.seq, now, actor, action, changes))
        if len(self.history) > UNDO_DEPTH:
            self.history.pop(0)

    def remove_player(self, actor: str, action: dict[str, Any], pid: str) -> None:
        """Take a player out of the game. Their commander damage on others stays (it still counts),
        and the whole player is logged so undo can restore their seat, totals and token."""
        seat = list(self.players).index(pid)
        player = self.players.pop(pid)
        self.departed[pid] = player.name
        self.seq += 1
        self.history.append(LogEntry(self.seq, self.clock(), actor, action, [], removal=Removal(player, seat)))
        if len(self.history) > UNDO_DEPTH:
            self.history.pop(0)

    def reorder(self, order: Any) -> None:
        """Seat players in the given order. It must list every current player exactly once."""
        if not isinstance(order, list) or sorted(map(str, order)) != sorted(self.players) or len(order) != len(self.players):
            raise ActionError("Seat order must list every player exactly once")
        self.players = {pid: self.players[pid] for pid in order}
        self.seq += 1  # not undoable, like rename: it changes no totals

    def _restore(self, removal: Removal) -> None:
        if len(self.players) >= MAX_PLAYERS:
            raise ActionError(f"Can't bring {removal.player.name} back: the room is full")
        seats = list(self.players.items())
        seats.insert(min(removal.seat, len(seats)), (removal.player.id, removal.player))
        self.players = dict(seats)
        self.departed.pop(removal.player.id, None)

    def undo(self) -> LogEntry:
        # Skip entries with nothing to reverse (e.g. renames)
        while self.history:
            entry = self.history.pop()
            if entry.removal:
                try:
                    self._restore(entry.removal)
                except ActionError:
                    self.history.append(entry)  # leave history as it was
                    raise
            elif entry.changes:
                for c in reversed(entry.changes):
                    self._apply_change(c, sign=-1)
            else:
                continue
            self.seq += 1
            if self.history:
                self.history[-1].closed = True
            return entry
        raise ActionError("Nothing to undo")

    def reset_game(self) -> None:
        for p in self.players.values():
            p.life = self.starting_life
            p.poison = 0
            p.cmd_damage.clear()
            p.counters.clear()
        self.history.clear()
        self.departed.clear()
        self.seq += 1
