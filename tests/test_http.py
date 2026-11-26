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
    r = client.get("/")
    assert "<title>Podside</title>" in r.text
    assert 'id="start"' in r.text and 'id="code"' in r.text  # start a game, join by code
    assert r.headers["cache-control"] == "no-cache"  # browsers pick up new files after a deploy


@pytest.mark.parametrize("path", ["display", "play"])
def test_view_pages(client: TestClient, room_code: str, path: str) -> None:
    r = client.get(f"/{path}/{room_code.lower()}")
    assert r.headers["content-type"].startswith("text/html")
    assert f'<script src="/{path}.js"></script>' in r.text


@pytest.mark.parametrize("asset", ["pips.css", "pips.js", "index.css", "index.js", "display.css", "display.js", "play.css", "play.js"])
def test_page_assets_are_served(client: TestClient, asset: str) -> None:
    assert client.get(f"/{asset}").status_code == 200


def test_config_js(client: TestClient) -> None:
    r = client.get("/config.js")
    assert r.headers["content-type"].startswith("text/javascript")
    assert "const POISON_LETHAL = 10, CMD_LETHAL = 21;" in r.text
    assert 'const COLORS = ["#e5484d", ' in r.text


def test_unknown_static_path_404(client: TestClient) -> None:
    assert client.get("/nope.js").status_code == 404
    assert client.get("/display").status_code == 404


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
