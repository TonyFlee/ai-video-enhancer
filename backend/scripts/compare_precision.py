"""Compare FP32 vs FP16 RIFE models on the GPU: speed, memory headroom, and output parity."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODELS = Path(__file__).resolve().parent.parent / "models"
from app.config import register_nvidia_dll_dirs  # noqa: E402


def make_session(model: Path):
    register_nvidia_dll_dirs()
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3
    session = ort.InferenceSession(
        str(model), sess_options=opts,
        providers=[("CUDAExecutionProvider", {
            "cudnn_conv_algo_search": "HEURISTIC",
            "cudnn_conv_use_max_workspace": "1",
            "arena_extend_strategy": "kSameAsRequested",
            "do_copy_in_default_stream": True,
        })],
    )
    return session


def run(session, a, b, timestep_name, runs=4):
    names = [i.name for i in session.get_inputs()]
    ts = "timestep" if any(n == "timestep" for n in names) else timestep_name
    def feed():
        pa = np.transpose(a.astype(np.float32) / 255.0, (2, 0, 1))
        pb = np.transpose(b.astype(np.float32) / 255.0, (2, 0, 1))
        d = {names[0]: np.concatenate([pa, pb], axis=0)[None, ...]}
        for n in names[1:]:
            d[n] = np.array(0.5, dtype=np.float32)
        return d

    f = feed()
    out = session.run(None, f)[0]
    times = []
    for _ in range(runs):
        start = time.time()
        session.run(None, f)
        times.append(time.time() - start)
    return min(times), sum(times) / len(times), out


def vram():
    import subprocess
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0] + " MB free"
    except Exception:  # noqa: BLE001
        return "?"


def main() -> int:
    fp32 = MODELS / "RIFE_fp32_timestep.onnx"
    fp16 = MODELS / "RIFE_fp16_timestep.onnx"
    sizes = [(640, 360), (1280, 720), (1080, 1920)]

    session32 = make_session(fp32)
    session16 = make_session(fp16)
    print("fp32 providers:", session32.get_providers())
    print("fp16 providers:", session16.get_providers())

    for (w, h) in sizes:
        rng = np.random.default_rng(0)
        a = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        b = np.roll(a, 5, axis=1)
        print(f"\n=== {w}x{h} ({w * h / 1e6:.2f} MP) | {vram()} ===")
        results = {}
        for label, session in (("fp32", session32), ("fp16", session16)):
            try:
                best, mean, out = run(session, a, b, "timestep")
                results[label] = out
                print(f"  {label}: best={best:.3f}s mean={mean:.3f}s ({1 / mean:.2f} fps) out={out.shape} {out.dtype}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {label}: FAILED -> {str(exc)[:120]}")

        if len(results) == 2:
            o32, o16 = results["fp32"], results["fp16"]
            n = min(o32.shape[-2], o16.shape[-2]), min(o32.shape[-1], o16.shape[-1])
            c32 = np.clip(o32[0, :3, :n[0], :n[1]], 0, 1)
            c16 = np.clip(o16[0, :3, :n[0], :n[1]], 0, 1)
            mae = float(np.mean(np.abs(c32 - c16)))
            psnr = 10 * np.log10(1.0 / max(float(np.mean((c32 - c16) ** 2)), 1e-12))
            print(f"  fp16 vs fp32 parity: MAE={mae:.5f} PSNR={psnr:.2f} dB")
    print(f"\nfinal VRAM: {vram()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
