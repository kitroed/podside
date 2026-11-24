"""Unit tests for the domain model and room manager (no network)."""

from __future__ import annotations

import time

import pytest

from models import MAX_PLAYERS, ROOM_IDLE_TTL_SECONDS, UNDO_DEPTH, ActionError, Room
from rooms import RoomManager


def test_player_starts_at_room_starting_life() -> None:
    room = Room(code="ABCD", starting_life=20)
    p = room.add_player("  Kit  ")
    assert (p.id, p.name, p.life) == ("p1", "Kit", 20)


def test_blank_name_falls_back_to_id() -> None:
    assert Room(code="ABCD").add_player("").name == "P1"


def test_name_is_truncated() -> None:
    assert len(Room(code="ABCD").add_player("x" * 100).name) == 24


def test_room_full() -> None:
    room = Room(code="ABCD")
    for i in range(MAX_PLAYERS):
        room.add_player(f"p{i}")
    with pytest.raises(ActionError, match="full"):
        room.add_player("one too many")


def test_public_has_no_token() -> None:
    p = Room(code="ABCD").add_player("Kit")
    assert "token" not in p.public()
    assert p.token not in str(p.public())


@pytest.mark.parametrize(
    ("life", "poison", "cmd", "eliminated"),
    [
        (1, 9, {"p2": 20}, False),
        (0, 0, {}, True),
        (5, 10, {}, True),
        (21, 0, {"p2": 21}, True),
        (30, 0, {"p2": 11, "p3": 10}, False),  # 21 total, but not from a single source
    ],
)
def test_eliminated(life: int, poison: int, cmd: dict[str, int], eliminated: bool) -> None:
    p = Room(code="ABCD").add_player("Kit")
    p.life, p.poison, p.cmd_damage = life, poison, cmd
    assert p.eliminated is eliminated


def test_commit_and_undo_round_trip() -> None:
    room = Room(code="ABCD")
    p = room.add_player("Kit")
    room.commit("p1", {"type": "x"}, [("p1", "life", None, -7), ("p1", "counters", "energy", 2)])
    assert (p.life, p.counters) == (33, {"energy": 2})
    room.undo()
    assert (p.life, p.counters) == (40, {})  # zeroed counters are removed, not left at 0
    assert room.seq == 2


def test_undo_with_empty_history() -> None:
    with pytest.raises(ActionError, match="Nothing to undo"):
        Room(code="ABCD").undo()


def test_history_is_capped() -> None:
    room = Room(code="ABCD")
    p = room.add_player("Kit")
    for _ in range(UNDO_DEPTH + 5):
        room.commit("p1", {}, [("p1", "life", None, -1)])
    assert len(room.history) == UNDO_DEPTH
    for _ in range(UNDO_DEPTH):
        room.undo()
    assert p.life == 40 - 5  # the oldest 5 fell off the log
    with pytest.raises(ActionError):
        room.undo()


def test_reset_keeps_players_and_clears_history() -> None:
    room = Room(code="ABCD", starting_life=20)
    p = room.add_player("Kit")
    room.commit("p1", {}, [("p1", "life", None, -3), ("p1", "poison", None, 2)])
    p.cmd_damage["p2"] = 4
    room.reset_game()
    assert (p.life, p.poison, p.cmd_damage, p.counters) == (20, 0, {}, {})
    assert room.history == []
    assert list(room.players) == ["p1"]


def test_snapshot_shape() -> None:
    room = Room(code="ABCD")
    room.add_player("Kit")
    snap = room.snapshot()
    assert set(snap) == {"code", "seq", "starting_life", "table_edits", "players", "last_action"}
    assert snap["last_action"] is None


def test_manager_codes_and_lookup() -> None:
    mgr = RoomManager()
    room = mgr.create(starting_life=30)
    assert len(room.code) == 4 and not set(room.code) & {"I", "O"}
    assert mgr.get(room.code.lower()) is room
    assert mgr.get("ZZZZ") is None


def test_remove_stale_skips_active_and_connected_rooms() -> None:
    mgr = RoomManager()
    idle, busy, connected = mgr.create(), mgr.create(), mgr.create()
    now = time.time()
    idle.last_activity = connected.last_activity = now - ROOM_IDLE_TTL_SECONDS - 1
    connected.connections.add(object())  # type: ignore[arg-type]
    assert mgr.remove_stale(now) == [idle.code]
    assert set(mgr.rooms) == {busy.code, connected.code}
