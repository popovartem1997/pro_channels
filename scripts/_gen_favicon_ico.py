"""One-off: write static/favicon.ico (16/32 px) without external deps. Run: python scripts/_gen_favicon_ico.py"""
from __future__ import annotations

import struct
from pathlib import Path

# Brand fill BGRA (little-endian in file): #5b6ef5
BGRA = bytes([0xF5, 0x6E, 0x5B, 0xFF])


def _dib_for_size(size: int) -> bytes:
    """XOR bitmap (BGRA, bottom-up) + AND mask (1 bpp, padded rows)."""
    w = h = size
    row = BGRA * w
    xor = b"".join(row for _ in range(h))
    mask_row_bytes = ((w + 31) // 32) * 4
    and_mask = b"\x00" * (mask_row_bytes * h)
    bi_size = 40
    bi_width = w
    bi_height = h * 2
    bi_planes = 1
    bi_bit_count = 32
    bi_compression = 0
    bi_size_image = w * h * 4
    bi_xppm = bi_yppm = 0
    bi_clr_used = bi_clr_important = 0
    header = struct.pack(
        "<IIIHHIIIIII",
        bi_size,
        bi_width,
        bi_height,
        bi_planes,
        bi_bit_count,
        bi_compression,
        bi_size_image,
        bi_xppm,
        bi_yppm,
        bi_clr_used,
        bi_clr_important,
    )
    return header + xor + and_mask


def build_ico(sizes: tuple[int, ...] = (16, 32)) -> bytes:
    images = [(s, _dib_for_size(s)) for s in sizes]
    reserved = 0
    icon_type = 1
    count = len(images)
    header = struct.pack("<HHH", reserved, icon_type, count)
    offset = 6 + count * 16
    entries = []
    blobs = []
    for w, dib in images:
        img_size = len(dib)
        entries.append(
            struct.pack(
                "<BBBBHHII",
                w if w < 256 else 0,
                w if w < 256 else 0,
                0,
                0,
                1,
                32,
                img_size,
                offset,
            )
        )
        blobs.append(dib)
        offset += img_size
    return header + b"".join(entries) + b"".join(blobs)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out = root / "static" / "favicon.ico"
    out.write_bytes(build_ico())
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
