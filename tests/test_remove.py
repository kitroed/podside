"""Removing players: the TV, the player themselves, or anyone with table edits on. Undoable."""

from __future__ import annotations

from conftest import Table, player
from models import MAX_PLAYERS


def ids(state: dict) -> list[str]:
    return [p["id"] for p in state["players"]]


def test_display_removes_a_player(table: Table) -> None:
    d = table.display()
    table.player("A"), table.player("B"), table.player("C")
    s = table.act(d, type="remove", target="p2")
    assert ids(s) == ["p1", "p3"]
    assert s["last_action"]["action"] == {"type": "remove", "target": "p2", "name": "B"}
    assert s["last_action"]["actor"] == "display"


def test_player_can_leave(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)  # leaving is allowed even with table edits off
    assert ids(table.act(b, type="remove")) == ["p1"]  # target defaults to self


def test_remove_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="remove", target=b.player_id)


def test_table_edits_lets_players_remove_others(table: Table) -> None:
    a = table.player("A")
    table.player("A again")  # the duplicate-join case
    assert ids(table.act(a, type="remove", target="p2")) == ["p1"]


def test_remove_unknown_player(table: Table) -> None:
    d = table.display()
    assert "Unknown target" in table.reject(d, type="remove", target="p9")


def test_undo_restores_seat_totals_and_position(table: Table) -> None:
    d = table.display()
    a, b, c = table.player("A"), table.player("B", colors="UG"), table.player("C")
    table.act(b, type="life", delta=-7)
    table.act(b, type="cmd_damage", source="p3", delta=4)
    before = player(table.act(b, type="poison", delta=2), "p2")
    table.act(d, type="remove", target="p2")
    s = table.act(d, type="undo")
    assert ids(s) == ["p1", "p2", "p3"]  # back in the same seat
    assert player(s, "p2") == before
    # Earlier history still undoes exactly once the player is back.
    assert player(table.act(d, type="undo"), "p2")["poison"] == 0


def test_removed_players_connection(table: Table) -> None:
    d = table.display()
    table.player("A")
    b = table.player("B")
    table.act(d, type="remove", target="p2")
    assert "Unknown target" in table.reject(b, type="life", delta=-1)
    b.send(type="join", name="B")  # the same phone joins again: a new seat
    assert b.welcome()["player_id"] == "p3"
    table._read_broadcast()


def test_undo_reconnects_the_removed_phone(table: Table) -> None:
    d = table.display()
    table.player("A")
    b = table.player("B")
    table.act(d, type="remove", target="p2")
    table.act(d, type="undo")
    assert player(table.act(b, type="life", delta=-1), "p2")["life"] == 39  # same connection works again


def test_removed_token_no_longer_reclaims_a_seat(table: Table) -> None:
    d = table.display()
    b = table.player("B")
    token = b.token
    table.act(d, type="remove", target="p1")
    table.drop(b)
    again = table.reconnect(token)  # type: ignore[arg-type]
    assert again.player_id is None
    assert "Join the room first" in table.reject(again, type="life", delta=-1)


def test_their_commander_damage_stays_and_keeps_a_name(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("Sam")
    table.act(a, type="cmd_damage", source="p2", delta=21)
    s = table.act(d, type="remove", target="p2")
    assert player(s, "p1")["cmd_damage"] == {"p2": 21}
    assert player(s, "p1")["eliminated"] is True  # nobody comes back to life
    assert s["departed"] == {"p2": "Sam"}
    s = table.act(d, type="undo")
    assert s["departed"] == {}


def test_departed_only_lists_names_still_needed(table: Table) -> None:
    d = table.display()
    table.player("A"), table.player("B")
    assert table.act(d, type="remove", target="p2")["departed"] == {}  # dealt no commander damage


def test_removing_frees_a_seat(table: Table) -> None:
    d = table.display()
    for i in range(MAX_PLAYERS):
        table.player(f"P{i}")
    table.act(d, type="remove", target="p3")
    extra = table.player("Late")
    assert extra.player_id == f"p{MAX_PLAYERS + 1}"  # ids are never reused


def test_undo_refused_when_the_room_filled_up(table: Table) -> None:
    d = table.display()
    for i in range(MAX_PLAYERS):
        table.player(f"P{i}")
    table.act(d, type="remove", target="p3")
    late = table.player("Late")  # takes the free seat
    assert "Can't bring P2 back: the room is full" in table.reject(d, type="undo")
    assert "room is full" in table.reject(d, type="undo")  # history left as it was
    s = table.act(late, type="life", delta=-1)  # the room carries on normally
    assert "p3" not in ids(s) and len(ids(s)) == MAX_PLAYERS


def test_reset_clears_departed(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    table.act(a, type="cmd_damage", source="p2", delta=3)
    table.act(d, type="remove", target="p2")
    assert table.act(d, type="reset")["departed"] == {}
