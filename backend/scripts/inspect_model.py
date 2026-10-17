"""Introspect the RIFE ONNX model and run a smoke interpolation test."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RIFE_MODEL_PATH  # noqa: E402
from app.rife import RifeEngine  # noqa: E402


def main() -> None:
    print(f"model: {RIFE_MODEL_PATH} (exists={RIFE_MODEL_PATH.exists()})")

    import onnxruntime as ort

    print("onnxruntime:", ort.__version__)
    print("available providers:", ort.get_available_providers())

    session = ort.InferenceSession(str(RIFE_MODEL_PATH), providers=["CPUExecutionProvider"])
    print("\ninputs:")
    for i in session.get_inputs():
        print(f"  {i.name:20s} shape={i.shape} type={i.type}")
    print("outputs:")
    for o in session.get_outputs():
        print(f"  {o.name:20s} shape={o.shape} type={o.type}")

    engine = RifeEngine(RIFE_MODEL_PATH, prefer_gpu=True)
    print("\nengine:", engine.describe())

    h, w = 128, 128
    y = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    a = np.repeat(y, w, axis=1)[:, :, None].repeat(3, axis=2)
    b = np.roll(a, 8, axis=1)

    start = time.time()
    mid = engine.interpolate(a, b, 0.5)
    elapsed = time.time() - start
    print(f"interpolate 0.5: shape={mid.shape} dtype={mid.dtype} "
          f"mean={mid.mean():.2f} min={mid.min()} max={mid.max()} time={elapsed * 1000:.1f} ms")

    if engine.supports_timestep:
        quarter = engine.interpolate(a, b, 0.25)
        print(f"interpolate 0.25: shape={quarter.shape} mean={quarter.mean():.2f}")

    # Larger frame to expose VRAM issues on 4 GB cards.
    for (th, tw) in ((720, 1280), (1080, 1920)):
        big_a = np.random.randint(0, 255, (th, tw, 3), dtype=np.uint8)
        big_b = np.roll(big_a, 4, axis=1)
        start = time.time()
        try:
            out = engine.interpolate(big_a, big_b, 0.5 if engine.supports_timestep else None)
            dt = time.time() - start
            print(f"resolution {tw}x{th}: ok in {dt:.2f}s -> {out.shape} device={engine.device} tiling={engine.tiling}")
        except Exception as exc:  # noqa: BLE001
            print(f"resolution {tw}x{th}: FAILED -> {exc}")

    print("\nfinal:", engine.describe())


if __name__ == "__main__":
    main()
