#!/usr/bin/env python3
"""Generate simple solid-color PNG icons for the PWA manifest."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "static" / "icons"


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def write_png(path: Path, size: int, rgba=(15, 23, 42, 255)) -> None:
    r, g, b, a = rgba
    row = bytes([r, g, b, a] * size)
    raw = b"".join(b"\x00" + row for _ in range(size))
    compressed = zlib.compress(raw, level=9)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", ihdr)
    png += _chunk(b"IDAT", compressed)
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    write_png(ICON_DIR / "icon-192.png", 192)
    write_png(ICON_DIR / "icon-512.png", 512)
    print(f"Wrote icons to {ICON_DIR}")


if __name__ == "__main__":
    main()
