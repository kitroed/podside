"""RoomManager: creates and looks up rooms, and drops idle ones in the background."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time

from models import (
    CLEANUP_INTERVAL_SECONDS,
    DEFAULT_STARTING_LIFE,
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    ROOM_IDLE_TTL_SECONDS,
    Room,
)

log = logging.getLogger("podside")


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create(self, starting_life: int = DEFAULT_STARTING_LIFE) -> Room:
        for _ in range(100):
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if code not in self.rooms:
                room = Room(code=code, starting_life=starting_life)
                self.rooms[code] = room
                log.info("Created room %s", code)
                return room
        raise RuntimeError("Could not allocate a room code")

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code.upper())

    def remove_stale(self, now: float | None = None) -> list[str]:
        """Drop rooms idle past the TTL that have no open connections."""
        cutoff = (now if now is not None else time.time()) - ROOM_IDLE_TTL_SECONDS
        stale = [c for c, r in self.rooms.items() if r.last_activity < cutoff and not r.connections]
        for code in stale:
            del self.rooms[code]
            log.info("Removed idle room %s", code)
        return stale

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            self.remove_stale()


manager = RoomManager()
