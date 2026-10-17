"""Benchmark RIFE inference to tune the VRAM strategy for this GPU.

Compares, at several resolutions:
  * full-frame inference with the default cuDNN workspace policy
  * full-frame inference with the small-workspace policy
  * tiled inference at several tile heights

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe scripts\\benchmark.py
    .\\.venv\\Scripts\\python.exe scripts\\benchmark.py --sizes 1280x720 1080x1920
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RIFE_MODEL_PATH, register_nvidia_dll_dirs  # noqa: E402
from app.rife import RifeEngine  # noqa: E402


def make_session(max_workspace: bool):
    register_nvidia_dll_dirs()
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3
    return ort.InferenceSession(
        str(RIFE_MODEL_PATH),
        sess_options=opts,
        providers=[("CUDAExecutionProvider", {
            "cudnn_conv_algo_search": "HEURISTIC",
            "cudnn_conv_use_max_workspace": "1" if max_workspace else "0",
            "arena_extend_strategy": "kSameAsRequested",
            "do_copy_in_default_stream": True,
        })],
    )


def build_feed(session, a, b, timestep_name):
    pa = np.transpose(a.astype(np.float32) / 255.0, (2, 0, 1))
    pb = np.transpose(b.astype(np.float32) / 255.0, (2, 0, 1))
    names = [i.name for i in session.get_inputs()]
    feed = {names[0]: np.concatenate([pa, pb], axis=0)[None, ...]}
    if timestep_name:
        feed[timestep_name] = np.array(0.5, dtype=np.float32)
    return feed


def time_session(session, width, height, runs=3, timestep_name="timestep"):
    rng = np.random.default_rng(1)
    a = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    b = np.roll(a, 5, axis=1)
    feed = build_feed(session, a, b, timestep_name)
    session.run(None, feed)  # warm up / algorithm search
    times = []
    for _ in range(runs):
        start = time.time()
        session.run(None, feed)
        times.append(time.time() - start)
    return min(times), sum(times) / len(times)


def vram_free():
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()[0]
        return int(out)
    except Exception:  # noqa: BLE001
        return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="*", default=["640x360", "1280x720", "1080x1920"])
    parser.add_argument("--tiles", nargs="*", type=int, default=[128, 192, 256, 384])
    args = parser.parse_args()

    print("available:", ort.get_available_providers())
    sessions = {
        "max-workspace": make_session(max_workspace=True),
        "small-workspace": make_session(max_workspace=False),
    }
    for label, session in sessions.items():
        if "CUDAExecutionProvider" not in session.get_providers():
            print(f"{label}: CUDA unavailable, skipping")
            return 1

    for size in args.sizes:
        width, height = (int(part) for part in size.lower().split("x"))
        print(f"\n=== {width}x{height} ===")
        for label, session in sessions.items():
            best, mean = time_session(session, width, height)
            print(f"  full-frame/{label:16s}: best={best:.3f}s mean={mean:.3f}s "
                  f"({1 / mean:.2f} fps) vram_free={vram_free()}MB")

        for tile in args.tiles:
            engine = RifeEngine(RIFE_MODEL_PATH, prefer_gpu=True)
            engine._use_tiles = True          # noqa: SLF001 - benchmark harness
            engine._tile_height = tile        # noqa: SLF001
            rng = np.random.default_rng(2)
            a = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
            b = np.roll(a, 5, axis=1)
            engine.interpolate(a, b, 0.5)
            times = []
            for _ in range(2):
                start = time.time()
                engine.interpolate(a, b, 0.5)
                times.append(time.time() - start)
            print(f"  tiled/{tile:4d}px{'':12s}: best={min(times):.3f}s mean={sum(times) / len(times):.3f}s "
                  f"({1 / (sum(times) / len(times)):.2f} fps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
