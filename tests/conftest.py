"""Shared fixtures: a TestClient, a fresh room, and a `Table` helper that drives
several WebSocket connections and keeps their message queues in step."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app import app
from rooms import manager


@pytest.fixture
def client() -> Iterator[TestClient]:
    manager.rooms.clear()
    with TestClient(app) as c:
        yield c
    manager.rooms.clear()


class FakeClock:
    """Room clock for tests. Each reading advances by `step` seconds; the default
    of 1s keeps actions outside the grouping window, like a slow player."""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 1_000_000.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def room_code(client: TestClient, clock: FakeClock) -> str:
    code = client.post("/api/rooms").json()["code"]
    room = manager.get(code)
    assert room is not None
    room.clock = clock
    return code


class Conn:
    """One WebSocket connection with typed receive helpers."""

    def __init__(self, ws: WebSocketTestSession, role: str) -> None:
        self.ws = ws
        self.role = role
        self.player_id: str | None = None
        self.token: str | None = None
        self.messages: list[dict[str, Any]] = []  # every message received, for leak checks

    def send(self, **msg: Any) -> None:
        self.ws.send_json(msg)

    def recv(self) -> dict[str, Any]:
        m = self.ws.receive_json()
        self.messages.append(m)
        return m

    def expect(self, kind: str) -> dict[str, Any]:
        m = self.recv()
        assert m["type"] == kind, f"expected {kind!r}, got {m!r}"
        return m

    def state(self) -> dict[str, Any]:
        return self.expect("state")["state"]

    def welcome(self) -> dict[str, Any]:
        m = self.expect("welcome")
        self.player_id, self.token = m["player_id"], m["token"]
        return m


class Table:
    """A room plus its open connections. Every broadcast is read off every
    connection so no queue falls behind."""

    def __init__(self, client: TestClient, code: str, stack: ExitStack) -> None:
        self.client = client
        self.code = code
        self.stack = stack
        self.conns: list[Conn] = []

    def _open(self, role: str, token: str | None = None) -> Conn:
        url = f"/ws/{self.code}?role={role}" + (f"&token={token}" if token else "")
        conn = Conn(self.stack.enter_context(self.client.websocket_connect(url)), role)
        if token and role == "player":
            # A known token gets a welcome first; an unknown one goes straight to state.
            m = conn.recv()
            if m["type"] == "welcome":
                conn.player_id, conn.token = m["player_id"], m["token"]
                conn.state()
            else:
                assert m["type"] == "state"
        else:
            conn.state()
        self.conns.append(conn)
        return conn

    def display(self) -> Conn:
        return self._open("display")

    def player(self, name: str = "", *, join: bool = True, colors: str | None = None) -> Conn:
        conn = self._open("player")
        if join:
            conn.send(type="join", name=name, **({"colors": colors} if colors is not None else {}))
            conn.welcome()
            self._read_broadcast()
        return conn

    def reconnect(self, token: str) -> Conn:
        return self._open("player", token=token)

    def drop(self, conn: Conn) -> None:
        """Close a connection, as when a phone locks or loses wifi."""
        conn.ws.close()
        self.conns.remove(conn)

    def restrict_edits(self, conn: Conn) -> None:
        """Turn table edits off so players can only change their own totals."""
        assert self.act(conn, type="settings", table_edits=False)["table_edits"] is False

    def act(self, conn: Conn, **msg: Any) -> dict[str, Any]:
        """Send an action that should succeed; return the broadcast state."""
        conn.send(**msg)
        return self._read_broadcast()

    def reject(self, conn: Conn, **msg: Any) -> str:
        """Send an action that should fail; return the error, sent to that client only."""
        conn.send(**msg)
        return conn.expect("error")["message"]

    def _read_broadcast(self) -> dict[str, Any]:
        states = [c.state() for c in self.conns]
        assert all(s == states[0] for s in states), "connections saw different states"
        return states[0]


@pytest.fixture
def table(client: TestClient, room_code: str) -> Iterator[Table]:
    with ExitStack() as stack:
        yield Table(client, room_code, stack)


def player(state: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in state["players"] if p["id"] == pid)
