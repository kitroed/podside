"""Minimal QR code encoder: byte mode, error correction level M, rendered as SVG.

Written so the display can show a join code without a QR dependency.
Follows ISO/IEC 18004; the structure mirrors Project Nayuki's reference
QR generator (MIT licence).
"""

from __future__ import annotations

from collections.abc import Callable

# Per version (index 1-40) for error correction level M.
ECC_CODEWORDS_PER_BLOCK = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
                           26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
NUM_BLOCKS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
              17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
ECL_M_FORMAT_BITS = 0b00

MASKS: tuple[Callable[[int, int], bool], ...] = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


# ---- capacity --------------------------------------------------------------------


def _raw_data_modules(ver: int) -> int:
    """Modules left for data and ECC once function patterns are placed."""
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_data_modules(ver) // 8 - ECC_CODEWORDS_PER_BLOCK[ver] * NUM_BLOCKS[ver]


# ---- Reed-Solomon over GF(2^8) with polynomial 0x11D -----------------------------------


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _add_ecc_and_interleave(data: list[int], ver: int) -> list[int]:
    numblocks = NUM_BLOCKS[ver]
    ecclen = ECC_CODEWORDS_PER_BLOCK[ver]
    rawcodewords = _raw_data_modules(ver) // 8
    numshort = numblocks - rawcodewords % numblocks
    shortlen = rawcodewords // numblocks
    divisor = _rs_divisor(ecclen)

    blocks: list[list[int]] = []
    k = 0
    for i in range(numblocks):
        block = data[k : k + shortlen - ecclen + (0 if i < numshort else 1)]
        k += len(block)
        ecc = _rs_remainder(block, divisor)
        if i < numshort:
            block.append(0)  # padding so every block has the same length; skipped below
        blocks.append(block + ecc)

    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != shortlen - ecclen or j >= numshort:
                result.append(block[i])
    return result


# ---- the module grid ---------------------------------------------------------------


class _Grid:
    def __init__(self, ver: int) -> None:
        self.ver = ver
        self.size = ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.is_function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.is_function[y][x] = True


def _alignment_positions(ver: int) -> list[int]:
    if ver == 1:
        return []
    numalign = ver // 7 + 2
    step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
    size = ver * 4 + 17
    return sorted([size - 7 - i * step for i in range(numalign - 1)] + [6])


def _draw_function_patterns(g: _Grid) -> None:
    size = g.size
    for i in range(size):  # timing patterns
        g.set_function(6, i, i % 2 == 0)
        g.set_function(i, 6, i % 2 == 0)

    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders plus separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    g.set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))

    pos = _alignment_positions(g.ver)
    last = len(pos) - 1
    for i, ax in enumerate(pos):
        for j, ay in enumerate(pos):
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue  # these corners hold finder patterns
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    g.set_function(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    _draw_format_bits(g, 0)  # reserve the area; redrawn once the mask is chosen
    _draw_version_bits(g)


def _draw_format_bits(g: _Grid, mask: int) -> None:
    data = ECL_M_FORMAT_BITS << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412

    def bit(i: int) -> bool:
        return ((bits >> i) & 1) != 0

    size = g.size
    for i in range(6):
        g.set_function(8, i, bit(i))
    g.set_function(8, 7, bit(6))
    g.set_function(8, 8, bit(7))
    g.set_function(7, 8, bit(8))
    for i in range(9, 15):
        g.set_function(14 - i, 8, bit(i))
    for i in range(8):
        g.set_function(size - 1 - i, 8, bit(i))
    for i in range(8, 15):
        g.set_function(8, size - 15 + i, bit(i))
    g.set_function(8, size - 8, True)  # always dark


def _draw_version_bits(g: _Grid) -> None:
    if g.ver < 7:
        return
    rem = g.ver
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    bits = g.ver << 12 | rem
    for i in range(18):
        dark = ((bits >> i) & 1) != 0
        a, b = g.size - 11 + i % 3, i // 3
        g.set_function(a, b, dark)
        g.set_function(b, a, dark)


def _draw_codewords(g: _Grid, codewords: list[int]) -> None:
    """Fill non-function modules in the zigzag order, two columns at a time."""
    size = g.size
    i = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5  # skip the vertical timing column
        upward = ((right + 1) & 2) == 0
        for vert in range(size):
            y = size - 1 - vert if upward else vert
            for x in (right, right - 1):
                if not g.is_function[y][x] and i < len(codewords) * 8:
                    g.dark[y][x] = ((codewords[i >> 3] >> (7 - (i & 7))) & 1) != 0
                    i += 1
        right -= 2


def _apply_mask(g: _Grid, mask: int) -> None:
    fn = MASKS[mask]
    for y in range(g.size):
        for x in range(g.size):
            if not g.is_function[y][x] and fn(x, y):
                g.dark[y][x] = not g.dark[y][x]


# ---- mask scoring (lower is easier to scan) ----------------------------------------------


def _finder_like_count(history: list[int]) -> int:
    n = history[1]
    core = n > 0 and history[2] == history[4] == history[5] == n and history[3] == n * 3
    return (int(core and history[0] >= n * 4 and history[6] >= n)
            + int(core and history[6] >= n * 4 and history[0] >= n))


def _add_run(runlen: int, history: list[int], size: int) -> None:
    if history[0] == 0:
        runlen += size  # the light border before the first run
    history.pop()
    history.insert(0, runlen)


def _penalty(g: _Grid) -> int:
    size, dark = g.size, g.dark
    result = 0
    columns = [list(col) for col in zip(*dark)]
    for line in [*dark, *columns]:
        color, runlen, history = False, 0, [0] * 7
        for module in line:
            if module == color:
                runlen += 1
                if runlen == 5:
                    result += 3
                elif runlen > 5:
                    result += 1
            else:
                _add_run(runlen, history, size)
                if not color:
                    result += _finder_like_count(history) * 40
                color, runlen = module, 1
        if color:
            _add_run(runlen, history, size)
            runlen = 0
        _add_run(runlen + size, history, size)  # the light border after the last run
        result += _finder_like_count(history) * 40

    for y in range(size - 1):
        for x in range(size - 1):
            if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                result += 3

    total = size * size
    darkcount = sum(map(sum, dark))
    result += ((abs(darkcount * 20 - total * 10) + total - 1) // total - 1) * 10
    return result


# ---- public API --------------------------------------------------------------------


def encode(text: str) -> list[list[bool]]:
    """Return the QR code for text as rows of modules (True is dark)."""
    data = text.encode("utf-8")
    for ver in range(1, 41):
        countbits = 8 if ver < 10 else 16
        capacity = _data_codewords(ver) * 8
        if 4 + countbits + len(data) * 8 <= capacity:
            break
    else:
        raise ValueError("Text is too long for a QR code")

    bits: list[int] = []

    def append(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    append(0b0100, 4)  # byte mode
    append(len(data), countbits)
    for b in data:
        append(b, 8)
    append(0, min(4, capacity - len(bits)))  # terminator
    append(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        append(pad, 8)
        pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]

    g = _Grid(ver)
    _draw_function_patterns(g)
    _draw_codewords(g, _add_ecc_and_interleave(codewords, ver))

    best_mask, best_score = 0, None
    for mask in range(8):
        _apply_mask(g, mask)
        _draw_format_bits(g, mask)
        score = _penalty(g)
        if best_score is None or score < best_score:
            best_mask, best_score = mask, score
        _apply_mask(g, mask)  # masks are XOR, so applying again undoes it
    _apply_mask(g, best_mask)
    _draw_format_bits(g, best_mask)
    return g.dark


def svg(text: str, border: int = 4) -> str:
    """Black-on-white SVG with the standard 4-module quiet zone."""
    modules = encode(text)
    dim = len(modules) + border * 2
    path = "".join(
        f"M{x + border},{y + border}h1v1h-1z"
        for y, row in enumerate(modules)
        for x, dark in enumerate(row)
        if dark
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges">'
        f'<rect width="{dim}" height="{dim}" fill="#fff"/><path d="{path}" fill="#000"/></svg>'
    )
