"""Seat order: the display, or players with table edits on. Not undoable (it changes no totals)."""

from __future__ import annotations

import pytest

from conftest import Table, player


def ids(state: dict) -> list[str]:
    return [p["id"] for p in state["players"]]


def test_display_reorders(table: Table) -> None:
    d = table.display()
    for name in "ABC":
        table.player(name)
    assert ids(table.act(d, type="reorder", order=["p3", "p1", "p2"])) == ["p3", "p1", "p2"]


@pytest.mark.parametrize(
    "order",
    [["p1", "p2"], ["p1", "p2", "p2"], ["p1", "p2", "p9"], ["p1", "p2", "p3", "p4"], "p1,p2,p3", None],
)
def test_reorder_must_list_every_player_once(table: Table, order: object) -> None:
    d = table.display()
    for name in "ABC":
        table.player(name)
    assert "exactly once" in table.reject(d, type="reorder", order=order)


def test_reorder_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "table display" in table.reject(a, type="reorder", order=["p2", "p1"])


def test_players_can_reorder_with_table_edits(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    assert ids(table.act(b, type="reorder", order=["p2", "p1"])) == ["p2", "p1"]


def test_reorder_is_not_undone(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    table.act(a, type="life", delta=-3)
    table.act(d, type="reorder", order=["p2", "p1"])
    s = table.act(d, type="undo")  # undoes the life change; the seats stay
    assert ids(s) == ["p2", "p1"]
    assert player(s, "p1")["life"] == 40


def test_removed_player_returns_to_their_seat_in_the_new_order(table: Table) -> None:
    d = table.display()
    for name in "ABC":
        table.player(name)
    table.act(d, type="reorder", order=["p3", "p2", "p1"])
    table.act(d, type="remove", target="p2")
    assert ids(table.act(d, type="undo")) == ["p3", "p2", "p1"]
