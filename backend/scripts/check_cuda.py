"""Check whether the CUDA execution provider can initialise, and benchmark it.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe scripts\check_cuda.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RIFE_MODEL_PATH  # noqa: E402
from app.rife import RifeEngine  # noqa: E402


def main() -> int:
    import onnxruntime as ort

    print("onnxruntime:", ort.__version__)
    print("providers:", ort.get_available_providers())

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        print("CUDAExecutionProvider not compiled into this build.")
        return 1

    try:
        session = ort.InferenceSession(
            str(RIFE_MODEL_PATH),
            providers=[("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})],
        )
    except Exception as exc:  # noqa: BLE001
        print("CUDA session creation FAILED:", exc)
        return 1

    print("session providers:", session.get_providers())
    if "CUDAExecutionProvider" not in session.get_providers():
        print("CUDA provider fell back to CPU - CUDA runtime is not usable.")
        return 1

    engine = RifeEngine(RIFE_MODEL_PATH, prefer_gpu=True)
    print("engine:", engine.describe())

    for (width, height) in ((256, 256), (1280, 720), (1080, 1920)):
        a = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
        b = np.roll(a, 6, axis=1)
        start = time.time()
        try:
            engine.interpolate(a, b, 0.5 if engine.supports_timestep else None)
            first = time.time() - start
            start = time.time()
            engine.interpolate(a, b, 0.5 if engine.supports_timestep else None)
            second = time.time() - start
            print(f"  {width}x{height}: first={first:.3f}s steady={second:.3f}s "
                  f"({1 / second:.1f} fps) device={engine.device} tiling={engine.tiling}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {width}x{height}: FAILED -> {exc}")

    print("\nfinal:", engine.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
