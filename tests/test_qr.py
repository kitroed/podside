"""QR encoder checks. A real reader (zxing-cpp) was used to confirm decoding while
developing; these tests guard the structure and round-trip the small sizes."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

import qr

Matrix = list[list[bool]]


def version_of(m: Matrix) -> int:
    return (len(m) - 17) // 4


def read_format_mask(m: Matrix) -> int:
    """Read the top-left format bits, check the BCH code, and return the mask."""
    bits = 0
    coords = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    for i, (x, y) in enumerate(coords):
        bits |= int(m[y][x]) << i
    raw = bits ^ 0x5412
    data = raw >> 10
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    assert raw == (data << 10 | rem), "format bits fail their BCH check"
    assert data >> 3 == 0b00, "error correction level should be M"
    return data & 0b111


def decode_single_block(m: Matrix) -> str:
    """Independent decoder for versions 1-3 (one RS block, no interleaving)."""
    size, ver = len(m), version_of(m)
    assert ver <= 3
    mask = qr.MASKS[read_format_mask(m)]
    reserved = [[False] * size for _ in range(size)]

    def reserve(x0: int, y0: int, w: int, h: int) -> None:
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                reserved[y][x] = True

    reserve(0, 0, 9, 9)                  # top-left finder + format
    reserve(size - 8, 0, 8, 9)           # top-right finder + format
    reserve(0, size - 8, 9, 8)           # bottom-left finder + format + dark module
    reserve(6, 0, 1, size)               # timing column
    reserve(0, 6, size, 1)               # timing row
    if ver >= 2:
        reserve(size - 9, size - 9, 5, 5)  # single alignment pattern

    bits: list[int] = []
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if ((size - 1 - col) // 2) % 2 == 0 else range(size)
        for y in rows:
            for x in (col, col - 1):
                if not reserved[y][x]:
                    bits.append(int(m[y][x] ^ mask(x, y)))
        col -= 2

    def take(n: int) -> int:
        nonlocal bits
        value = int("".join(map(str, bits[:n])), 2)
        bits = bits[n:]
        return value

    assert take(4) == 0b0100, "expected byte mode"
    length = take(8)
    return bytes(take(8) for _ in range(length)).decode("utf-8")


# ---- structure ----------------------------------------------------------------------


@pytest.mark.parametrize(("text", "version"), [("a", 1), ("x" * 14, 1), ("x" * 15, 2), ("x" * 106, 6), ("x" * 107, 7), ("x" * 213, 10), ("x" * 214, 11)])
def test_smallest_version_is_chosen(text: str, version: int) -> None:
    m = qr.encode(text)
    assert version_of(m) == version
    assert len(m) == version * 4 + 17 and all(len(row) == len(m) for row in m)


@pytest.mark.parametrize("text", ["a", "https://podside.example.com/play/ABCD", "x" * 150])
def test_function_patterns(text: str) -> None:
    m = qr.encode(text)
    size = len(m)
    finder = [[max(abs(dx), abs(dy)) != 2 for dx in range(-3, 4)] for dy in range(-3, 4)]
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        assert [row[cx - 3 : cx + 4] for row in m[cy - 3 : cy + 4]] == finder
    assert [m[6][x] for x in range(8, size - 8)] == [x % 2 == 0 for x in range(8, size - 8)]
    assert [m[y][6] for y in range(8, size - 8)] == [y % 2 == 0 for y in range(8, size - 8)]
    assert m[size - 8][8] is True  # the always-dark module
    read_format_mask(m)


@pytest.mark.parametrize(
    "text",
    ["a", "MTG", "https://podside.example.com/play/ABCD", "http://192.168.1.50:8086/play/WXYZ", "Ünïcødé 🃏", "z" * 42],
)
def test_round_trip_small_versions(text: str) -> None:
    assert decode_single_block(qr.encode(text)) == text


def test_join_link_matches_verified_output() -> None:
    # Pinned from output that zxing-cpp decoded correctly; fails if encoding changes.
    m = qr.encode("https://podside.example.com/play/ABCD")
    digest = hashlib.sha256("".join("1" if c else "0" for row in m for c in row).encode()).hexdigest()
    assert digest == "3de33695364f92a21961b1bf0ba37e934f6c024b5e33146f7776b6b43857b537"


def test_too_long() -> None:
    with pytest.raises(ValueError, match="too long"):
        qr.encode("x" * 3000)


def test_svg() -> None:
    out = qr.svg("a")
    assert out.startswith("<svg") and 'viewBox="0 0 29 29"' in out  # 21 modules + 4 border each side


# ---- endpoint ------------------------------------------------------------------------


def test_room_qr_endpoint(client: TestClient, room_code: str) -> None:
    r = client.get(f"/api/rooms/{room_code.lower()}/qr.svg", params={"base": "https://podside.example.com"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.text == qr.svg(f"https://podside.example.com/play/{room_code}")


def test_room_qr_unknown_room(client: TestClient) -> None:
    assert client.get("/api/rooms/ZZZZ/qr.svg", params={"base": "http://x"}).status_code == 404


@pytest.mark.parametrize("base", ["javascript:alert(1)", "https://evil.example/phish", "ftp://x", "x" * 300])
def test_room_qr_rejects_bad_base(client: TestClient, room_code: str, base: str) -> None:
    assert client.get(f"/api/rooms/{room_code}/qr.svg", params={"base": base}).status_code == 422


def test_room_qr_requires_base(client: TestClient, room_code: str) -> None:
    assert client.get(f"/api/rooms/{room_code}/qr.svg").status_code == 422
