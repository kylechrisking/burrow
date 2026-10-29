"""A small QR code encoder (byte mode, error correction level M, versions 1-10:
up to 213 bytes), so `add-user --totp` can show a code to scan without a
dependency. Follows ISO/IEC 18004 and Project Nayuki's reference encoder.

    m = encode("otpauth://totp/...")
    print(terminal(m))   # or svg(m)
"""
from __future__ import annotations

# version -> (EC codewords per block, [(blocks, data codewords per block), ...]) at level M
_BLOCKS = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_ECL_M = 0   # format bits for level M


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


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """Pick the smallest version that fits; return it with the final interleaved codewords."""
    for version, (ec_len, groups) in _BLOCKS.items():
        capacity = sum(n * k for n, k in groups)
        count_bits = 8 if version < 10 else 16
        need = 4 + count_bits + 8 * len(data)
        if need <= capacity * 8:
            break
    else:
        raise ValueError(f"too long for a version 10 QR code ({len(data)} bytes)")
    bits: list[int] = []

    def put(value: int, n: int):
        bits.extend((value >> i) & 1 for i in reversed(range(n)))
    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, capacity * 8 - len(bits)))
    put(0, -len(bits) % 8)
    stream = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(stream) < capacity:
        stream.append(pad)
        pad ^= 0xEC ^ 0x11

    divisor = _rs_divisor(ec_len)
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            d = stream[pos:pos + k]
            pos += k
            blocks.append((d, _rs_remainder(d, divisor)))
    out = [d[i] for i in range(max(len(d) for d, _ in blocks)) for d, _ in blocks if i < len(d)]
    out += [e[i] for i in range(ec_len) for _, e in blocks]
    return version, out


def encode(text: str) -> list[list[bool]]:
    """The QR matrix for `text` (True = dark), without the quiet zone."""
    version, words = _codewords(text.encode("utf-8"))
    size = version * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x: int, y: int, dark: bool):
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    pos = _ALIGN.get(version, [])
    last = len(pos) - 1
    for i, ay in enumerate(pos):
        for j, ax in enumerate(pos):
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    _format(setf, size, 0)        # reserve the format areas; drawn for real after masking
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vbits = version << 12 | rem
        for i in range(18):
            bit = (vbits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, bit)
            setf(b, a, bit)

    i, total = 0, len(words) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < total:
                    mod[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    best = None
    for mask in range(8):
        cand = [row[:] for row in mod]
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and _MASKS[mask](x, y):
                    cand[y][x] = not cand[y][x]

        def setc(x, y, dark, c=cand):
            c[y][x] = dark
        _format(setc, size, mask)
        score = _penalty(cand)
        if best is None or score < best[0]:
            best = (score, cand)
    return best[1]


_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]


def _format(setm, size: int, mask: int) -> None:
    data = _ECL_M << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412

    def bit(i):
        return (bits >> i) & 1 == 1
    for i in range(6):
        setm(8, i, bit(i))
    setm(8, 7, bit(6))
    setm(8, 8, bit(7))
    setm(7, 8, bit(8))
    for i in range(9, 15):
        setm(14 - i, 8, bit(i))
    for i in range(8):
        setm(size - 1 - i, 8, bit(i))
    for i in range(8, 15):
        setm(8, size - 15 + i, bit(i))
    setm(8, size - 8, True)


def _penalty(m: list[list[bool]]) -> int:
    """ISO 18004 mask scoring: runs, 2x2 blocks, finder-like patterns, balance.
    Any mask decodes; this only picks the one scanners like best."""
    size = len(m)
    score = 0
    lines = m + [list(col) for col in zip(*m)]
    finder = ([True, False, True, True, True, False, True, False, False, False, False],
              [False, False, False, False, True, False, True, True, True, False, True])
    for line in lines:
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, v
        if run >= 5:
            score += run - 2
        for i in range(size - 10):
            if line[i:i + 11] in finder:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    total = size * size
    score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
    return score


def terminal(m: list[list[bool]], border: int = 2) -> str:
    """Two rows per line with half blocks. The light modules are the drawn
    ones, which suits the usual light-text-on-dark terminal."""
    size = len(m)
    full = [[False] * (size + 2 * border) for _ in range(border)]
    full += [[False] * border + row + [False] * border for row in m]
    full += [[False] * (size + 2 * border) for _ in range(border + 1)]
    chars = {(False, False): "█", (True, False): "▄", (False, True): "▀", (True, True): " "}
    return "\n".join("".join(chars[(full[y][x], full[y + 1][x])] for x in range(len(full[0])))
                     for y in range(0, size + 2 * border, 2))


def svg(m: list[list[bool]], border: int = 4) -> str:
    n = len(m) + 2 * border
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
