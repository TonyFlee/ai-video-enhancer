"""Compare tiled vs full-frame RIFE output to confirm tiling does not create seams.

Runs full-frame and tiled inference on the same pair (at a size that fits both) and
reports the max / mean absolute pixel difference. Big values at tile boundaries would
indicate the overlap is too small for the model's motion range.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RIFE_MODEL_PATH  # noqa: E402
from app.rife import RifeEngine  # noqa: E402


def main() -> int:
    width, height = 640, 640
    rng = np.random.default_rng(11)
    # Structured content (gradients + blobs) moves more realistically than pure noise.
    yy, xx = np.mgrid[0:height, 0:width]
    base = ((np.sin(xx / 19.0) + np.cos(yy / 23.0)) * 0.5 + 0.5)
    a = np.stack([base, np.roll(base, 13, axis=1), np.roll(base, 7, axis=0)], axis=-1)
    a = (a * 255).astype(np.uint8)
    b = np.roll(a, 6, axis=1)

    engine = RifeEngine(RIFE_MODEL_PATH, prefer_gpu=True)
    engine._use_tiles = False  # noqa: SLF001
    full = engine.interpolate(a, b, 0.5)

    results = {}
    for overlap in (32, 64):
        engine._use_tiles = True          # noqa: SLF001
        engine._tile_height = 256         # noqa: SLF001
        engine._tile_overlap = overlap    # noqa: SLF001
        tiled = engine.interpolate(a, b, 0.5)
        diff = np.abs(tiled.astype(np.int16) - full.astype(np.int16))
        results[overlap] = (int(diff.max()), float(diff.mean()))

    print(f"device={engine.device} size={width}x{height} tile=256")
    for overlap, (mx, mean) in results.items():
        print(f"  overlap={overlap:4d}  max|diff|={mx:3d}  mean|diff|={mean:.3f}")

    # Locate the worst rows to distinguish a visible full-width seam from isolated pixels.
    engine._use_tiles = True          # noqa: SLF001
    engine._tile_height = 256         # noqa: SLF001
    engine._tile_overlap = 32         # noqa: SLF001
    tiled = engine.interpolate(a, b, 0.5)
    diff = np.abs(tiled.astype(np.int16) - full.astype(np.int16))
    row_mean = diff.mean(axis=(1, 2))
    row_max = diff.max(axis=(1, 2))
    print("worst rows by mean:")
    for y in np.argsort(row_mean)[::-1][:6]:
        print(f"   y={int(y):4d}  row_mean={row_mean[y]:.3f}  row_max={int(row_max[y]):3d}  "
              f"cols_over_10={(diff[int(y)] > 10).sum()}")
    print(f"pixels with |diff|>10: {(diff > 10).sum()} / {diff.size}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
