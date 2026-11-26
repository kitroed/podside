"""Deck colors: set on join or with the colors action, shown as pips."""

from __future__ import annotations

import pytest

from conftest import Table, player
from models import ActionError, clean_colors


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ""), (None, ""), ("gw", "WG"), ("GUW", "WUG"), ("rrr", "R"), ("W U B R G", "WUBRG"), ("c", "C")],
)
def test_clean_colors(raw: str | None, expected: str) -> None:
    assert clean_colors(raw) == expected


@pytest.mark.parametrize(("raw", "message"), [("WX", "letters"), ("CW", "Colorless"), (["W"], "string"), (5, "string")])
def test_clean_colors_rejects(raw: object, message: str) -> None:
    with pytest.raises(ActionError, match=message):
        clean_colors(raw)


def test_new_players_have_no_colors(table: Table) -> None:
    a = table.player("Kit")
    assert player(a.messages[-1]["state"], "p1")["colors"] == ""


def test_join_with_colors(table: Table) -> None:
    a = table.player("Kit", colors="ugw")
    assert player(a.messages[-1]["state"], "p1")["colors"] == "WUG"


def test_join_with_bad_colors_takes_no_seat(table: Table) -> None:
    p = table.player(join=False)
    assert "letters" in table.reject(p, type="join", name="Kit", colors="WZ")
    p.send(type="join", name="Kit", colors="W")
    assert p.welcome()["player_id"] == "p1"  # the failed join didn't use up p1


def test_set_colors(table: Table) -> None:
    a = table.player("Kit")
    s = table.act(a, type="colors", colors="BRG")
    assert player(s, "p1")["colors"] == "BRG"
    s = table.act(a, type="colors", colors="")
    assert player(s, "p1")["colors"] == ""


def test_set_colors_rejects_bad_input(table: Table) -> None:
    a = table.player("Kit")
    assert "Colorless" in table.reject(a, type="colors", colors="CR")


def test_set_colors_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="colors", target=b.player_id, colors="W")


def test_table_edits_and_display_can_set_colors(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    assert player(table.act(a, type="colors", target=b.player_id, colors="U"), "p2")["colors"] == "U"
    assert player(table.act(d, type="colors", target=a.player_id, colors="C"), "p1")["colors"] == "C"


def test_colors_are_not_undone(table: Table) -> None:
    a = table.player("Kit")
    table.act(a, type="life", delta=-3)
    table.act(a, type="colors", colors="R")
    s = table.act(a, type="undo")  # undoes the life change, keeps the colors
    assert player(s, "p1")["colors"] == "R"
    assert player(s, "p1")["life"] == 40


def test_colors_survive_reset_and_reconnect(table: Table) -> None:
    a = table.player("Kit", colors="B")
    table.act(a, type="reset")
    token = a.token
    table.drop(a)
    again = table.reconnect(token)  # type: ignore[arg-type]
    assert player(again.messages[-1]["state"], "p1")["colors"] == "B"
