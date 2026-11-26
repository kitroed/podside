"""Saving a game to a file and resuming it in a new room."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import Table, player
from rooms import manager
from saves import MAX_SAVE_BYTES


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as s:
        yield s


def save_of(client: TestClient, code: str) -> dict[str, Any]:
    r = client.get(f"/api/rooms/{code}/save")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == "attachment"
    return r.json()


def resume(client: TestClient, save: Any) -> str:
    body = save if isinstance(save, (str, bytes)) else json.dumps(save)
    r = client.post("/api/rooms/resume", content=body)
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert r.json()["display_url"] == f"/display/{code}"
    return code


def resume_error(client: TestClient, save: Any) -> str:
    body = save if isinstance(save, (str, bytes)) else json.dumps(save)
    r = client.post("/api/rooms/resume", content=body)
    assert r.status_code == 400, r.text
    return r.json()["detail"]


def minimal(**overrides: Any) -> dict[str, Any]:
    save = {
        "format": "podside-save",
        "version": 1,
        "starting_life": 40,
        "table_edits": True,
        "players": [{"id": "p1", "name": "Kit", "life": 31}, {"id": "p2", "name": "Sam", "life": 40}],
    }
    return {**save, **overrides}


def played_game(table: Table) -> dict[str, Any]:
    """Three players with a bit of everything, then Lee leaves after dealing commander damage."""
    d = table.display()
    kit, sam, lee = table.player("Kit", colors="WU"), table.player("Sam", colors="BRG"), table.player("Lee")
    table.act(kit, type="life", delta=-7)
    table.act(kit, type="poison", delta=3)
    table.act(sam, type="cmd_damage", source=kit.player_id, delta=6)
    table.act(sam, type="counter", name="energy", delta=2)
    table.act(kit, type="cmd_damage", source=lee.player_id, delta=4)
    table.act(d, type="settings", starting_life=30, table_edits=False)
    table.act(d, type="reorder", order=[sam.player_id, kit.player_id, lee.player_id])
    return table.act(d, type="remove", target=lee.player_id)


def test_save_contents(client: TestClient, table: Table) -> None:
    played_game(table)
    save = save_of(client, table.code)
    assert (save["format"], save["version"], save["code"]) == ("podside-save", 1, table.code)
    assert (save["starting_life"], save["table_edits"]) == (30, False)
    assert [p["name"] for p in save["players"]] == ["Sam", "Kit"]  # seat order
    kit = save["players"][1]
    assert kit == {"id": "p1", "name": "Kit", "colors": "WU", "life": 40 - 7 - 4, "poison": 3,
                   "cmd_damage": {"p3": 4}, "counters": {}}
    assert save["departed"] == {"p3": "Lee"}


def test_save_has_no_tokens(client: TestClient, table: Table) -> None:
    a, b = table.player("Kit"), table.player("Sam")
    text = client.get(f"/api/rooms/{table.code}/save").text
    assert a.token not in text and b.token not in text and "token" not in text


def test_save_unknown_room(client: TestClient) -> None:
    assert client.get("/api/rooms/ZZZZ/save").status_code == 404


def test_round_trip(client: TestClient, table: Table) -> None:
    before = played_game(table)
    code = resume(client, save_of(client, table.code))
    assert code != table.code
    after = client.get(f"/api/rooms/{code}").json()
    for key in ("starting_life", "table_edits", "departed"):
        assert after[key] == before[key]
    strip = lambda s: [{k: v for k, v in p.items() if k != "claimed"} for p in s["players"]]  # noqa: E731
    assert strip(after) == strip(before)
    assert all(p["claimed"] is False for p in after["players"])
    assert after["last_action"] is None


def test_resumed_room_has_nothing_to_undo(client: TestClient, stack: ExitStack) -> None:
    t = Table(client, resume(client, minimal()), stack)
    d = t.display()
    assert "Nothing to undo" in t.reject(d, type="undo")


def test_claim_seat(client: TestClient, stack: ExitStack) -> None:
    t = Table(client, resume(client, minimal()), stack)
    d = t.display()
    kit = t.claim("p1")
    s = d.messages[-1]["state"]
    assert kit.player_id == "p1"
    assert player(s, "p1")["claimed"] is True and player(s, "p2")["claimed"] is False
    s = t.act(kit, type="life", delta=-1)
    assert player(s, "p1")["life"] == 30


def test_claimed_seat_cannot_be_taken_again(client: TestClient, stack: ExitStack) -> None:
    t = Table(client, resume(client, minimal()), stack)
    t.claim("p1")
    other = t.player(join=False)
    assert "already taken Kit's seat" in t.reject(other, type="join", seat="p1")
    assert "isn't in this game" in t.reject(other, type="join", seat="p9")
    other.send(type="join", seat="p2")  # a free seat still works
    assert other.welcome()["player_id"] == "p2"


def test_claim_works_with_table_edits_off(client: TestClient, stack: ExitStack) -> None:
    t = Table(client, resume(client, minimal(table_edits=False)), stack)
    assert t.claim("p2").player_id == "p2"


def test_claimed_seat_reconnects_by_token(client: TestClient, stack: ExitStack) -> None:
    t = Table(client, resume(client, minimal()), stack)
    kit = t.claim("p1")
    token = kit.token
    assert token is not None
    t.drop(kit)
    again = t.reconnect(token)
    assert again.player_id == "p1"


def test_new_players_get_fresh_ids(client: TestClient, stack: ExitStack) -> None:
    save = minimal(players=[{"id": "p2", "name": "Sam", "life": 40}], departed={"p5": "Lee"})
    save["players"][0]["cmd_damage"] = {"p5": 3}
    t = Table(client, resume(client, save), stack)
    assert t.player("New").player_id == "p6"


def test_unclaimed_seat_can_be_removed_and_restored(client: TestClient, stack: ExitStack) -> None:
    t = Table(client, resume(client, minimal()), stack)
    d = t.display()
    s = t.act(d, type="remove", target="p2")
    assert [p["id"] for p in s["players"]] == ["p1"]
    s = t.act(d, type="undo")
    assert player(s, "p2")["claimed"] is False


def test_unclaimed_seat_has_no_token() -> None:
    from models import Room

    room = Room(code="ABCD")
    p = room.add_player("Kit")
    p.token = None
    assert room.player_by_token("") is None and room.player_by_token("anything") is None


@pytest.mark.parametrize(
    ("save", "message"),
    [
        ("not json", "isn't a saved Podside game"),
        ({"hello": "world"}, "isn't a saved Podside game"),
        (minimal(version=2), "newer version"),
        (minimal(players=[]), "players"),
        (minimal(players=[{"id": f"p{i}", "name": "x", "life": 40} for i in range(1, 10)]), "players"),
        (minimal(players=[{"id": "p1", "name": "A", "life": 40}, {"id": "p1", "name": "B", "life": 40}]), "unique"),
        (minimal(players=[{"id": "p1", "name": "A", "life": "40"}]), "players.0.life"),
        (minimal(players=[{"id": "p1", "name": "A", "life": 40, "colors": "WX"}]), "letters"),
        (minimal(players=[{"id": "x1", "name": "A", "life": 40}]), "players.0.id"),
        (minimal(players=[{"id": "p1", "name": "A", "life": 40, "cmd_damage": {"p7": 3}}]), "unknown player 'p7'"),
        (minimal(players=[{"id": "p1", "name": "A", "life": 10**9}]), "players.0.life"),
        (minimal(players=[{"id": "p1", "name": "A", "life": 40, "counters": {" ": 1}}]), "counter with no name"),
        (minimal(departed={"p1": "Kit"}), "departed"),
        (minimal(starting_life=0), "starting_life"),
    ],
)
def test_bad_saves_are_rejected(client: TestClient, save: Any, message: str) -> None:
    rooms_before = len(manager.rooms)
    assert message in resume_error(client, save)
    assert len(manager.rooms) == rooms_before  # nothing was created


def test_oversized_save_is_rejected(client: TestClient) -> None:
    assert "too big" in resume_error(client, b" " * (MAX_SAVE_BYTES + 1))


def test_resume_cleans_names_and_counters(client: TestClient) -> None:
    save = minimal(players=[{"id": "p1", "name": "  " + "x" * 50, "life": 40, "counters": {"Energy": 2, "rad": 0}}])
    code = resume(client, save)
    p = client.get(f"/api/rooms/{code}").json()["players"][0]
    assert (p["name"], p["counters"]) == ("x" * 24, {"energy": 2})
