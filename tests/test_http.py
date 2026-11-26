"""HTTP routes and WebSocket connection edge cases."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_create_room(client: TestClient) -> None:
    r = client.post("/api/rooms").json()
    assert r["display_url"] == f"/display/{r['code']}"
    assert r["play_url"] == f"/play/{r['code']}"
    assert client.get(f"/api/rooms/{r['code']}").json()["starting_life"] == 40


def test_create_room_with_starting_life(client: TestClient) -> None:
    code = client.post("/api/rooms?starting_life=20").json()["code"]
    assert client.get(f"/api/rooms/{code}").json()["starting_life"] == 20


def test_room_lookup_is_case_insensitive(client: TestClient, room_code: str) -> None:
    assert client.get(f"/api/rooms/{room_code.lower()}").json()["code"] == room_code


def test_unknown_room_404(client: TestClient) -> None:
    assert client.get("/api/rooms/ZZZZ").status_code == 404
    assert client.get("/display/ZZZZ").status_code == 404
    assert client.get("/play/ZZZZ").status_code == 404


def test_index(client: TestClient) -> None:
    html = client.get("/").text
    assert "<title>EDH Table</title>" in html
    assert 'id="start"' in html and 'id="code"' in html  # start a game, join by code
    assert "__" not in html  # pip CSS placeholder filled in


@pytest.mark.parametrize("path", ["display", "play"])
def test_view_pages(client: TestClient, room_code: str, path: str) -> None:
    html = client.get(f"/{path}/{room_code.lower()}").text
    assert f'const CODE = "{room_code}";' in html  # normalised to upper case
    assert "const POISON_LETHAL = 10, CMD_LETHAL = 21;" in html
    assert 'const COLORS = ["#e5484d", ' in html
    assert "__" not in html  # every placeholder was filled in


def test_ws_unknown_room(client: TestClient) -> None:
    with client.websocket_connect("/ws/ZZZZ?role=display") as ws:
        assert ws.receive_json() == {"type": "error", "message": "Room not found"}
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4404


def test_ws_bad_role_rejected(client: TestClient, room_code: str) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/{room_code}?role=admin") as ws:
            ws.receive_json()
