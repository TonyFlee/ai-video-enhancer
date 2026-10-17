"""Measure one RIFE configuration in a fresh process (avoids multi-session VRAM thrash).

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\bench_one.py --model fp32 --size 1280x720 --mode tile --tile 256
    .\\.venv\\Scripts\\python.exe scripts\\bench_one.py --model fp16 --size 1080x1920 --mode full

Prints a single JSON line so a driver script can collect results.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import MODELS_DIR, register_nvidia_dll_dirs  # noqa: E402
from app.rife import RifeEngine  # noqa: E402

MODELS = {
    "fp32": MODELS_DIR / "RIFE_fp32_timestep.onnx",
    "fp16": MODELS_DIR / "RIFE_fp16_timestep.onnx",
    "midpoint": MODELS_DIR / "RIFE_fp32.onnx",
}


def vram_free_mb() -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip().splitlines()[0]
        return int(out)
    except Exception:  # noqa: BLE001
        return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), default="fp32")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--mode", choices=["full", "tile"], default="full")
    parser.add_argument("--tile", type=int, default=256)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()

    width, height = (int(p) for p in args.size.lower().split("x"))
    register_nvidia_dll_dirs()

    result: dict = {
        "model": args.model,
        "size": f"{width}x{height}",
        "mode": args.mode,
        "tile": args.tile if args.mode == "tile" else None,
        "device": "cpu" if args.cpu else "gpu",
    }
    result["vram_free_mb_before"] = vram_free_mb()

    try:
        engine = RifeEngine(MODELS[args.model], prefer_gpu=not args.cpu)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"load failed: {str(exc)[:160]}"
        print(json.dumps(result))
        return 1

    if args.mode == "tile":
        engine._use_tiles = True       # noqa: SLF001 - benchmark harness
        engine._tile_height = args.tile  # noqa: SLF001

    rng = np.random.default_rng(7)
    a = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    b = np.roll(a, 7, axis=1)

    try:
        t0 = time.time()
        engine.interpolate(a, b, 0.5)
        result["warmup_s"] = round(time.time() - t0, 3)

        times = []
        for _ in range(args.runs):
            t0 = time.time()
            engine.interpolate(a, b, 0.5)
            times.append(time.time() - t0)
        result["best_s"] = round(min(times), 3)
        result["mean_s"] = round(sum(times) / len(times), 3)
        result["fps"] = round(1 / (sum(times) / len(times)), 2)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:200]

    result["device_used"] = engine.device
    result["engine_tiling"] = engine.tiling
    result["vram_free_mb_after"] = vram_free_mb()
    print(json.dumps(result))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
