"""Joining and reclaiming a seat by token."""

from __future__ import annotations

from conftest import Table, player


def test_reconnect_with_token_reclaims_seat(table: Table) -> None:
    d = table.display()
    a = table.player("Kit")
    table.act(a, type="life", delta=-7)
    token = a.token
    assert token
    table.drop(a)

    again = table.reconnect(token)
    assert (again.player_id, again.token) == ("p1", token)
    s = table.act(again, type="life", delta=-1)  # can act straight away, no join needed
    assert len(s["players"]) == 1
    assert player(s, "p1")["life"] == 32
    assert not any(m["type"] == "welcome" for m in d.messages)


def test_join_after_reconnect_keeps_the_same_seat(table: Table) -> None:
    a = table.player("Kit")
    token = a.token
    table.drop(a)
    again = table.reconnect(token)  # type: ignore[arg-type]
    again.send(type="join", name="Someone else")
    assert again.welcome()["player_id"] == "p1"
    s = again.state()
    assert [p["name"] for p in s["players"]] == ["Kit"]


def test_unknown_token_must_join_as_new_player(table: Table) -> None:
    table.player("A")
    stranger = table.reconnect("not-a-real-token")
    assert stranger.player_id is None
    assert "Join the room first" in table.reject(stranger, type="life", delta=-1)
    stranger.send(type="join", name="B")
    assert stranger.welcome()["player_id"] == "p2"


def test_joining_twice_on_one_connection_does_not_add_a_seat(table: Table) -> None:
    a = table.player("A")
    a.send(type="join", name="A again")
    assert a.welcome()["player_id"] == "p1"
    assert len(a.state()["players"]) == 1


def test_token_on_display_connection_is_ignored(table: Table, client, room_code: str) -> None:
    a = table.player("A")
    with client.websocket_connect(f"/ws/{room_code}?role=display&token={a.token}") as ws:
        assert ws.receive_json()["type"] == "state"  # no welcome, no token handed out


def test_two_connections_for_one_player(table: Table) -> None:
    # Phone reconnects before the server notices the old socket died.
    a = table.player("A")
    second = table.reconnect(a.token)  # type: ignore[arg-type]
    assert second.player_id == "p1"
    s = table.act(second, type="life", delta=-2)
    assert player(s, "p1")["life"] == 38
