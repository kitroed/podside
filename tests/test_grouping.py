"""Rapid repeats of the same action share one history entry (one undo step, one running total)."""

from __future__ import annotations

from conftest import FakeClock, Table, player
from models import GROUP_WINDOW_SECONDS
from rooms import manager


def fast(clock: FakeClock) -> None:
    clock.step = GROUP_WINDOW_SECONDS / 4


def test_rapid_taps_group(table: Table, clock: FakeClock) -> None:
    a = table.player("Kit")
    fast(clock)
    for _ in range(5):
        s = table.act(a, type="life", delta=-1)
    assert player(s, "p1")["life"] == 35  # every tap still applies straight away
    assert s["last_action"]["action"] == {"type": "life", "target": "p1", "delta": -5}
    assert player(table.act(a, type="undo"), "p1")["life"] == 40  # one undo reverses the group


def test_window_slides_with_each_tap(table: Table, clock: FakeClock) -> None:
    a = table.player("Kit")
    clock.step = GROUP_WINDOW_SECONDS * 0.9  # each tap is just inside the window of the previous one
    for _ in range(4):
        s = table.act(a, type="life", delta=-1)
    assert s["last_action"]["action"]["delta"] == -4


def test_slow_taps_stay_separate(table: Table, clock: FakeClock) -> None:
    a = table.player("Kit")
    clock.step = GROUP_WINDOW_SECONDS * 1.5
    table.act(a, type="life", delta=-1)
    s = table.act(a, type="life", delta=-1)
    assert s["last_action"]["action"]["delta"] == -1
    assert player(table.act(a, type="undo"), "p1")["life"] == 39


def test_mixed_signs_net_out(table: Table, clock: FakeClock) -> None:
    a = table.player("Kit")
    fast(clock)
    table.act(a, type="life", delta=-5)
    s = table.act(a, type="life", delta=1)
    assert s["last_action"]["action"]["delta"] == -4
    assert player(table.act(a, type="undo"), "p1")["life"] == 40


def test_group_that_nets_to_zero_is_dropped(table: Table, clock: FakeClock) -> None:
    a = table.player("Kit")
    fast(clock)
    table.act(a, type="life", delta=1)
    s = table.act(a, type="life", delta=-1)
    assert s["last_action"] is None
    assert "Nothing to undo" in table.reject(a, type="undo")


def test_different_actions_do_not_group(table: Table, clock: FakeClock) -> None:
    d = table.display()
    a, b = table.player("A"), table.player("B")
    fast(clock)
    steps = [
        (a, {"type": "life", "delta": -1}),
        (a, {"type": "poison", "delta": 1}),                                  # different type
        (a, {"type": "life", "target": "p2", "delta": -1}),                   # different target
        (b, {"type": "life", "target": "p2", "delta": -1}),                   # different actor
        (d, {"type": "life", "target": "p2", "delta": -1}),                   # the display is its own actor
        (a, {"type": "cmd_damage", "source": "p2", "delta": 1}),
        (a, {"type": "counter", "name": "energy", "delta": 1}),
        (a, {"type": "counter", "name": "experience", "delta": 1}),           # different counter
    ]
    for conn, msg in steps:
        table.act(conn, **msg)
    room = manager.get(table.code)
    assert room is not None and len(room.history) == len(steps)


def test_cmd_damage_groups_by_source_and_undoes_life_too(table: Table, clock: FakeClock) -> None:
    a = table.player("A")
    table.player("B")
    table.player("C")
    fast(clock)
    for _ in range(3):
        table.act(a, type="cmd_damage", source="p2", delta=1)
    s = table.act(a, type="cmd_damage", source="p3", delta=1)  # new source: new entry
    assert player(s, "p1")["cmd_damage"] == {"p2": 3, "p3": 1}
    s = table.act(a, type="undo")
    assert player(s, "p1")["cmd_damage"] == {"p2": 3}
    s = table.act(a, type="undo")
    assert player(s, "p1")["cmd_damage"] == {}
    assert player(s, "p1")["life"] == 40


def test_poison_clamp_inside_a_group_keeps_undo_exact(table: Table, clock: FakeClock) -> None:
    a = table.player("A")
    table.act(a, type="poison", delta=2)
    clock.now += 1  # a pause, so the +2 is its own entry
    fast(clock)
    for _ in range(4):
        s = table.act(a, type="poison", delta=-1)  # the last two clamp to nothing
    assert player(s, "p1")["poison"] == 0
    assert s["last_action"]["action"]["delta"] == -2  # only what was actually applied
    assert player(table.act(a, type="undo"), "p1")["poison"] == 2


def test_undo_then_tap_starts_a_new_group(table: Table, clock: FakeClock) -> None:
    a = table.player("A")
    fast(clock)
    table.act(a, type="life", delta=-1)
    table.act(a, type="life", delta=-1)
    table.act(a, type="undo")
    s = table.act(a, type="life", delta=-1)
    assert s["last_action"]["action"]["delta"] == -1
    assert player(s, "p1")["life"] == 39


def test_tap_after_undo_does_not_join_the_older_group(table: Table, clock: FakeClock) -> None:
    a = table.player("A")
    fast(clock)
    table.act(a, type="life", delta=-1)          # group 1
    table.act(a, type="poison", delta=1)         # group 2
    table.act(a, type="undo")                    # removes group 2; group 1 is last again, and recent
    s = table.act(a, type="life", delta=-1)
    assert s["last_action"]["action"]["delta"] == -1  # a new group, not -2
    assert player(table.act(a, type="undo"), "p1")["life"] == 39


def test_logged_action_ignores_extra_client_fields(table: Table) -> None:
    a = table.player("A")
    s = table.act(a, type="life", delta="-2", note="<script>", target="p1")
    assert s["last_action"]["action"] == {"type": "life", "target": "p1", "delta": -2}
