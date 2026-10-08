"""Draws disk_cleaner/icon.ico with the standard library only (run: python make_icon.py).

Design: teal rounded square, a white disk with a slice taken out (space freed) and a hub hole.
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "disk_cleaner" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # supersampling per axis

A, B = (9, 78, 74), (20, 150, 158)
WHITE = (255, 255, 255)
TINT = (153, 246, 228)


def in_round_rect(x, y, r):
    cx, cy = min(max(x, r), 1 - r), min(max(y, r), 1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def sample(x, y):
    """Colour (r, g, b, a) at unit-square point."""
    if not in_round_rect(x, y, 0.22):
        return (0, 0, 0, 0)
    t = (x + y) / 2
    bg = tuple(A[i] + (B[i] - A[i]) * t for i in range(3))
    dx, dy = x - 0.5, y - 0.52
    d = math.hypot(dx, dy)
    if d <= 0.09:
        return bg + (255,)
    if d <= 0.34:
        ang = math.degrees(math.atan2(-dy, dx)) % 360  # 0 = right, counter-clockwise
        if 20 <= ang <= 100:
            return TINT + (255,)
        return WHITE + (255,)
    return bg + (255,)


def render(n):
    rows = []
    for py in range(n):
        row = bytearray([0])
        for px in range(n):
            r = g = b = a = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    cr, cg, cb, ca = sample((px + (sx + .5) / SS) / n, (py + (sy + .5) / SS) / n)
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            if a:
                row += bytes((int(r / a), int(g / a), int(b / a), int(a / (SS * SS))))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    pngs = [(n, render(n)) for n in SIZES]
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for n, data in pngs:
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    OUT.write_bytes(head + entries + blobs)
    print("wrote", OUT, len(head + entries + blobs), "bytes")


if __name__ == "__main__":
    main()
