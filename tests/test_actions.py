"""Each action type over a real WebSocket: the normal case, a permission refusal, and undo."""

from __future__ import annotations

import pytest

from conftest import Table, player
from models import MAX_DELTA

# ---- life -------------------------------------------------------------------


def test_life(table: Table) -> None:
    a = table.player("Kit")
    s = table.act(a, type="life", delta=-3)  # target defaults to self
    assert player(s, "p1")["life"] == 37
    assert s["last_action"]["actor"] == "p1"
    assert s["last_action"]["action"] == {"type": "life", "target": "p1", "delta": -3}


def test_life_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="life", target=b.player_id, delta=-1)


def test_life_undo(table: Table) -> None:
    a = table.player("A")
    table.act(a, type="life", delta=-5)
    table.act(a, type="life", delta=2)
    assert player(table.act(a, type="undo"), "p1")["life"] == 35
    assert player(table.act(a, type="undo"), "p1")["life"] == 40


def test_life_can_go_negative(table: Table) -> None:
    a = table.player("A")
    s = table.act(a, type="life", delta=-45)
    assert player(s, "p1")["life"] == -5
    assert player(s, "p1")["eliminated"] is True


# ---- poison -----------------------------------------------------------------


def test_poison(table: Table) -> None:
    a = table.player("A")
    for _ in range(9):
        table.act(a, type="poison", delta=1)
    s = table.act(a, type="poison", delta=1)
    assert player(s, "p1")["poison"] == 10
    assert player(s, "p1")["eliminated"] is True


def test_poison_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="poison", target=b.player_id, delta=1)


def test_poison_clamp_keeps_undo_exact(table: Table) -> None:
    a = table.player("A")
    table.act(a, type="poison", delta=2)
    assert player(table.act(a, type="poison", delta=-5), "p1")["poison"] == 0
    # Undo restores 2 (the clamped -2), not 5.
    assert player(table.act(a, type="undo"), "p1")["poison"] == 2


def test_poison_below_zero_is_a_no_op(table: Table) -> None:
    a = table.player("A")
    s = table.act(a, type="poison", delta=-1)
    assert player(s, "p1")["poison"] == 0
    assert s["last_action"] is None  # nothing recorded, so nothing to undo
    assert "Nothing to undo" in table.reject(a, type="undo")


# ---- commander damage -------------------------------------------------------


def test_cmd_damage_also_lowers_life(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    s = table.act(a, type="cmd_damage", source=b.player_id, delta=5)
    assert player(s, "p1")["cmd_damage"] == {"p2": 5}
    assert player(s, "p1")["life"] == 35


def test_cmd_damage_lethal_from_one_source(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    s = table.act(a, type="cmd_damage", source=b.player_id, delta=21)
    assert player(s, "p1")["life"] == 19
    assert player(s, "p1")["eliminated"] is True


def test_cmd_damage_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="cmd_damage", target=b.player_id, source=a.player_id, delta=1)


@pytest.mark.parametrize("source", [None, "p1", "p9"])
def test_cmd_damage_invalid_source(table: Table, source: str | None) -> None:
    a = table.player("A")
    table.player("B")
    assert "source" in table.reject(a, type="cmd_damage", source=source, delta=1)


def test_cmd_damage_undo_reverts_both(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.act(a, type="cmd_damage", source=b.player_id, delta=4)
    s = table.act(a, type="undo")
    assert player(s, "p1")["cmd_damage"] == {}
    assert player(s, "p1")["life"] == 40


def test_cmd_damage_clamp_keeps_undo_exact(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.act(a, type="cmd_damage", source=b.player_id, delta=3)
    s = table.act(a, type="cmd_damage", source=b.player_id, delta=-10)
    assert player(s, "p1")["cmd_damage"] == {}
    assert player(s, "p1")["life"] == 40  # only 3 life came back, not 10
    s = table.act(a, type="undo")
    assert player(s, "p1")["cmd_damage"] == {"p2": 3}
    assert player(s, "p1")["life"] == 37


# ---- counters ---------------------------------------------------------------


def test_counter(table: Table) -> None:
    a = table.player("A")
    table.act(a, type="counter", name="Energy", delta=2)
    s = table.act(a, type="counter", name="energy ", delta=1)
    assert player(s, "p1")["counters"] == {"energy": 3}


def test_counter_requires_name(table: Table) -> None:
    a = table.player("A")
    assert "name required" in table.reject(a, type="counter", name="  ", delta=1)


def test_counter_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="counter", target=b.player_id, name="energy", delta=1)


def test_counter_undo(table: Table) -> None:
    a = table.player("A")
    table.act(a, type="counter", name="energy", delta=2)
    assert player(table.act(a, type="undo"), "p1")["counters"] == {}


# ---- rename -----------------------------------------------------------------


def test_rename(table: Table) -> None:
    a = table.player("A")
    s = table.act(a, type="rename", name="  Kit  ")
    assert player(s, "p1")["name"] == "Kit"


def test_rename_requires_name(table: Table) -> None:
    a = table.player("A")
    assert "Name required" in table.reject(a, type="rename", name="")


def test_rename_permission(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(a)
    assert "own totals" in table.reject(a, type="rename", target=b.player_id, name="Loser")


def test_rename_is_not_undone(table: Table) -> None:
    a = table.player("A")
    table.act(a, type="life", delta=-1)
    table.act(a, type="rename", name="Kit")
    s = table.act(a, type="undo")  # undoes the life change, keeps the name
    assert player(s, "p1")["name"] == "Kit"
    assert player(s, "p1")["life"] == 40


# ---- settings, reset, undo ----------------------------------------------------


def test_settings(table: Table) -> None:
    d = table.display()
    s = table.act(d, type="settings", starting_life=20, table_edits=False)
    assert (s["starting_life"], s["table_edits"]) == (20, False)


@pytest.mark.parametrize("life", [0, 1000])
def test_settings_starting_life_range(table: Table, life: int) -> None:
    d = table.display()
    assert "out of range" in table.reject(d, type="settings", starting_life=life)


@pytest.mark.parametrize("life", ["abc", None, [40], "4.5"])
def test_settings_starting_life_not_a_number(table: Table, life: object) -> None:
    d = table.display()
    assert "whole number" in table.reject(d, type="settings", starting_life=life)
    # The connection survives and the room is unchanged.
    assert table.act(d, type="settings", table_edits=False)["starting_life"] == 40


def test_reset_uses_new_starting_life(table: Table) -> None:
    d = table.display()
    a = table.player("A")
    table.act(a, type="life", delta=-10)
    table.act(d, type="settings", starting_life=20)
    s = table.act(d, type="reset")
    assert player(s, "p1")["life"] == 20
    assert s["last_action"] is None


def test_reset_cannot_be_undone(table: Table) -> None:
    a = table.player("A")
    table.act(a, type="life", delta=-10)
    table.act(a, type="reset")
    assert "Nothing to undo" in table.reject(a, type="undo")


def test_undo_is_room_wide(table: Table) -> None:
    a, b = table.player("A"), table.player("B")
    table.act(a, type="life", delta=-1)
    table.act(b, type="life", delta=-2)
    s = table.act(a, type="undo")  # the room's last action, whoever made it
    assert player(s, "p2")["life"] == 40
    assert player(s, "p1")["life"] == 39


# ---- permissions ------------------------------------------------------------


def test_table_edits_on_by_default(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    s = table.act(a, type="life", target=b.player_id, delta=-4)
    assert s["table_edits"] is True
    assert player(s, "p2")["life"] == 36
    assert s["last_action"]["actor"] == "p1"
    table.act(d, type="settings", table_edits=False)
    assert "own totals" in table.reject(a, type="life", target=b.player_id, delta=-1)


def test_display_can_edit_anyone(table: Table) -> None:
    d = table.display()
    table.player("A")
    b = table.player("B")
    s = table.act(d, type="life", target=b.player_id, delta=-6)
    assert player(s, "p2")["life"] == 34
    assert s["last_action"]["actor"] == "display"
    table.act(d, type="cmd_damage", target="p1", source="p2", delta=2)
    assert player(table.act(d, type="undo"), "p1")["life"] == 40


def test_display_needs_a_target(table: Table) -> None:
    d = table.display()
    table.player("A")
    assert "Unknown target" in table.reject(d, type="life", delta=-1)


def test_unknown_target(table: Table) -> None:
    a = table.player("A")
    assert "Unknown target" in table.reject(a, type="life", target="p9", delta=-1)


# ---- input validation ---------------------------------------------------------


@pytest.mark.parametrize("delta", [0, MAX_DELTA + 1, -MAX_DELTA - 1, "abc", None, [1]])
def test_bad_delta(table: Table, delta: object) -> None:
    a = table.player("A")
    assert "delta" in table.reject(a, type="life", delta=delta)


def test_numeric_string_delta_is_accepted(table: Table) -> None:
    a = table.player("A")
    assert player(table.act(a, type="life", delta="-2"), "p1")["life"] == 38


def test_unknown_action_type(table: Table) -> None:
    a = table.player("A")
    assert "Unknown action type" in table.reject(a, type="explode")


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"life"'])
def test_invalid_json(table: Table, raw: str) -> None:
    a = table.player("A")
    a.ws.send_text(raw)
    assert a.expect("error")["message"] == "Invalid JSON"
    # The connection is still usable afterwards.
    assert player(table.act(a, type="life", delta=-1), "p1")["life"] == 39


def test_ping(table: Table) -> None:
    a = table.player("A")
    a.send(type="ping")
    assert a.recv() == {"type": "pong"}


def test_action_before_join(table: Table) -> None:
    p = table.player(join=False)
    assert "Join the room first" in table.reject(p, type="life", delta=-1)


def test_display_cannot_join(table: Table) -> None:
    d = table.display()
    assert "Only player connections" in table.reject(d, type="join", name="TV")


def test_room_full_over_websocket(table: Table) -> None:
    for i in range(8):
        table.player(f"P{i}")
    extra = table.player(join=False)
    assert "Room is full" in table.reject(extra, type="join", name="Ninth")


# ---- multi-client -------------------------------------------------------------


def test_broadcast_reaches_everyone_and_seq_increases(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    seqs = [table.act(a, type="life", delta=-1)["seq"], table.act(b, type="life", delta=-1)["seq"]]
    seqs.append(table.act(d, type="undo")["seq"])
    assert seqs == sorted(set(seqs))
    # Table._read_broadcast already checked every connection saw identical state.


def test_simultaneous_taps_both_count(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    a.send(type="life", target="p1", delta=-1)
    b.send(type="life", target="p1", delta=-1)
    for c in table.conns:
        c.state()  # broadcast after the first tap
    final = [c.state() for c in table.conns]
    assert all(player(s, "p1")["life"] == 38 for s in final)


def test_errors_go_only_to_the_sender(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    table.restrict_edits(d)
    table.reject(a, type="life", target=b.player_id, delta=-1)
    # If anyone else had received that error, this broadcast would not be the next message.
    s = table.act(d, type="life", target="p1", delta=-1)
    assert player(s, "p1")["life"] == 39


def test_tokens_never_broadcast(table: Table) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    table.act(a, type="life", delta=-1)
    for conn in table.conns:
        for m in conn.messages:
            if m["type"] == "state":
                assert a.token not in str(m) and b.token not in str(m)
    assert not any(m["type"] == "welcome" for m in d.messages)
    assert [m["player_id"] for m in a.messages if m["type"] == "welcome"] == ["p1"]
    assert [m["player_id"] for m in b.messages if m["type"] == "welcome"] == ["p2"]
