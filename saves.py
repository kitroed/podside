"""Saved games: a JSON file a player downloads, and later uploads to resume in a new room.

A save holds totals and seats but never tokens, so restored seats start unclaimed and
each phone claims its seat again when it joins.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from models import MAX_PLAYERS, ActionError, Player, Room, clean_colors, clean_name

SAVE_FORMAT = "podside-save"
SAVE_VERSION = 1
MAX_SAVE_BYTES = 64 * 1024
MAX_TOTAL = 1_000_000  # sanity bound on any saved number
MAX_COUNTERS = 20
MAX_DEPARTED = 64

PLAYER_ID = r"^p[1-9][0-9]{0,3}$"


class SavedPlayer(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str = Field(pattern=PLAYER_ID)
    name: str = Field(max_length=100)
    colors: str = Field(default="", max_length=10)
    life: int = Field(ge=-MAX_TOTAL, le=MAX_TOTAL)
    poison: int = Field(default=0, ge=0, le=MAX_TOTAL)
    cmd_damage: dict[str, int] = Field(default_factory=dict, max_length=MAX_PLAYERS + MAX_DEPARTED)
    counters: dict[str, int] = Field(default_factory=dict, max_length=MAX_COUNTERS)

    @field_validator("colors")
    @classmethod
    def _colors(cls, v: str) -> str:
        try:
            return clean_colors(v)
        except ActionError as e:
            raise ValueError(str(e)) from None

    @field_validator("cmd_damage", "counters")
    @classmethod
    def _amounts(cls, v: dict[str, int]) -> dict[str, int]:
        if any(abs(n) > MAX_TOTAL for n in v.values()):
            raise ValueError("number out of range")
        return {k: n for k, n in v.items() if n}  # zeros are left out, as in a live game


class SaveFile(BaseModel):
    model_config = ConfigDict(strict=True)

    format: Literal["podside-save"]
    version: Literal[1]
    starting_life: int = Field(ge=1, le=999)
    table_edits: bool = True
    players: list[SavedPlayer] = Field(min_length=1, max_length=MAX_PLAYERS)
    departed: dict[str, str] = Field(default_factory=dict, max_length=MAX_DEPARTED)

    @model_validator(mode="after")
    def _consistent(self) -> SaveFile:
        ids = [p.id for p in self.players]
        if len(set(ids)) != len(ids):
            raise ValueError("player ids must be unique")
        for pid in self.departed:
            if not re.match(PLAYER_ID, pid) or pid in ids:
                raise ValueError(f"bad departed player id {pid!r}")
        known = set(ids) | set(self.departed)
        for p in self.players:
            unknown = set(p.cmd_damage) - known
            if unknown:
                raise ValueError(f"{p.id} has commander damage from unknown player {sorted(unknown)[0]!r}")
            if any(not clean_name(k) for k in p.counters):
                raise ValueError(f"{p.id} has a counter with no name")
        return self


def dump(room: Room) -> dict[str, Any]:
    """The room as a save file. Tokens and undo history are left out."""
    return {
        "format": SAVE_FORMAT,
        "version": SAVE_VERSION,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": room.code,  # for reference only; a resumed game gets a new code
        "starting_life": room.starting_life,
        "table_edits": room.table_edits,
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "colors": p.colors,
                "life": p.life,
                "poison": p.poison,
                "cmd_damage": dict(p.cmd_damage),
                "counters": dict(p.counters),
            }
            for p in room.players.values()
        ],
        "departed": room.departed_names(),
    }


def parse(raw: bytes) -> SaveFile:
    """Check an uploaded save. Raises ActionError with a message for the person uploading it."""
    if len(raw) > MAX_SAVE_BYTES:
        raise ActionError("That file is too big to be a saved game")
    try:
        return SaveFile.model_validate_json(raw)
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        if err["type"] == "json_invalid":
            raise ActionError("That file isn't a saved Podside game") from None
        if where in ("format", "version"):
            raise ActionError("That file isn't a saved Podside game (or it's from a newer version)") from None
        raise ActionError(f"That save file is damaged ({where}: {err['msg']})") from None


def restore(room: Room, save: SaveFile) -> None:
    """Fill a new, empty room from a save. Every seat starts unclaimed, with no undo history."""
    room.starting_life = save.starting_life
    room.table_edits = save.table_edits
    for sp in save.players:
        room.players[sp.id] = Player(
            id=sp.id,
            name=clean_name(sp.name) or sp.id.upper(),
            token=None,
            life=sp.life,
            poison=sp.poison,
            cmd_damage=dict(sp.cmd_damage),
            counters={clean_name(k).lower(): n for k, n in sp.counters.items()},
            colors=sp.colors,
        )
    room.departed = {pid: clean_name(name) or pid.upper() for pid, name in save.departed.items()}
    used = [int(pid[1:]) for pid in [*room.players, *room.departed]]
    room._next_player_num = max(used) + 1  # new players never reuse a saved id
    room.seq += 1
