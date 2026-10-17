"""Generate the application icon (packaging/app.ico) without any imaging library.

Kept dependency-free so it runs in the bare build venv (numpy is already required
by onnxruntime). Renders at SS x the target size and box-downsamples, which gives
clean anti-aliased edges on a gradient background.

    python packaging/make_icon.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

SIZES = (256, 48, 32, 16)
SS = 8  # supersampling factor

# Portfolio palette (src/app/projects/page.tsx): purple -> pink diagonal.
TOP = np.array([0x6C, 0x63, 0xFF], dtype=np.float32)
BOTTOM = np.array([0xFF, 0x3C, 0xA6], dtype=np.float32)


def _rounded_rect(x, y, x0, y0, x1, y1, rad):
    ix0, ix1, iy0, iy1 = x0 + rad, x1 - rad, y0 + rad, y1 - rad
    in_x = (x >= x0) & (x <= x1)
    in_y = (y >= y0) & (y <= y1)
    strip_v = (x >= ix0) & (x <= ix1)
    strip_h = (y >= iy0) & (y <= iy1)
    corners = (
        ((x - ix0) ** 2 + (y - iy0) ** 2 <= rad**2)
        | ((x - ix1) ** 2 + (y - iy0) ** 2 <= rad**2)
        | ((x - ix0) ** 2 + (y - iy1) ** 2 <= rad**2)
        | ((x - ix1) ** 2 + (y - iy1) ** 2 <= rad**2)
    )
    return in_x & in_y & ((strip_v & in_y) | (strip_h & in_x) | corners)


def _triangle(x, y, p0, p1, p2):
    def side(pa, pb):
        return (pa[0] - x) * (pb[1] - y) - (pb[0] - x) * (pa[1] - y)

    d1, d2, d3 = side(p0, p1), side(p1, p2), side(p2, p0)
    negative = (d1 < 0) | (d2 < 0) | (d3 < 0)
    positive = (d1 > 0) | (d2 > 0) | (d3 > 0)
    return ~(negative & positive)


def render(size: int) -> np.ndarray:
    """RGBA uint8 array of shape (size, size, 4)."""
    n = size * SS
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) / (n - 1)

    # Diagonal gradient inside a rounded square (4% margin so it sits nicely
    # next to the Windows shell's own padding).
    t = np.clip((xx + yy) / 2, 0, 1)[..., None]
    rgb = TOP * (1 - t) + BOTTOM * t
    plate = _rounded_rect(xx, yy, 0.04, 0.04, 0.96, 0.96, 0.20)

    # Motion: two trailing bars plus a bold play triangle.
    white = np.zeros((n, n), dtype=np.float32)
    white += 0.65 * _rounded_rect(xx, yy, 0.13, 0.34, 0.18, 0.66, 0.025)
    white += 0.45 * _rounded_rect(xx, yy, 0.21, 0.34, 0.26, 0.66, 0.025)
    white += _triangle(xx, yy, (0.34, 0.24), (0.34, 0.76), (0.78, 0.50))
    white = np.clip(white, 0, 1)[..., None]

    rgb = rgb * (1 - white) + 255.0 * white
    alpha = np.where(plate, 255.0, 0.0)

    # Box downsample by SS for clean anti-aliased edges.
    rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
    rgba = rgba.reshape(size, SS, size, SS, 4).mean(axis=(1, 3))
    return np.clip(rgba, 0, 255).astype(np.uint8)


def to_png(rgba: np.ndarray) -> bytes:
    height, width, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        data = kind + payload
        return struct.pack(">I", len(payload)) + data + struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def to_ico_entry(rgba: np.ndarray) -> bytes:
    """32-bit BGRA BMP entry (bottom-up) plus a blank AND mask."""
    height, width, _ = rgba.shape
    bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])  # RGBA -> BGRA
    flipped = bgra[::-1]
    header = struct.pack(
        "<IiiHHIIiiII",
        40, width, height * 2, 1, 32, 0, width * height * 4, 0, 0, 0, 0,
    )
    mask_stride = ((width + 31) // 32) * 4
    mask = bytes(mask_stride * height)
    return header + flipped.tobytes() + mask


def write_ico(path: Path) -> None:
    entries = [(size, to_ico_entry(render(size))) for size in SIZES]
    offset = 6 + 16 * len(entries)
    out = struct.pack("<HHH", 0, 1, len(entries))
    for size, blob in entries:
        out += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0, 0, 1, 32, len(blob), offset,
        )
        offset += len(blob)
    path.write_bytes(out + b"".join(blob for _, blob in entries))
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB) sizes={list(SIZES)}")


def main() -> None:
    target = Path(__file__).resolve().parent / "app.ico"
    write_ico(target)
    preview = target.with_name("app-icon-preview.png")
    preview.write_bytes(to_png(render(128)))
    print(f"wrote {preview}")


if __name__ == "__main__":
    main()
