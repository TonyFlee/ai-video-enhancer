"""Compare full-detail vs Fastest processing on the same clip, on the real GPU.

Runs two identical jobs against a running backend, differing only in `computeScale`,
and reports throughput plus the output geometry. Proves that *Faster*/*Fastest*
processing detail really speeds jobs up while keeping the requested output size.

Start the server first:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Then:
    .\\.venv\\Scripts\\python.exe scripts\\bench_processing_detail.py

Note: throughput depends on what else is using the GPU, so compare the two rows
captured in the same run rather than absolute numbers.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import FFMPEG, TMP_DIR, ensure_dirs  # noqa: E402

BASE = "http://127.0.0.1:8000"
CLIP = TMP_DIR / "bench_detail_clip.mp4"
TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 60


def make_clip() -> None:
    # 2 s of 720p30 with real motion, so 60 fps output means a 2x interpolation factor.
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(CLIP),
    ]
    subprocess.run(cmd, check=True, timeout=300)


def run_job(base: str, scale: float) -> dict:
    settings = {
        "targetFps": TARGET_FPS,
        "preset": "custom",
        "customWidth": TARGET_W,
        "customHeight": TARGET_H,
        "fitMode": "cover",
        "computeScale": scale,
        "codec": "h264",
        "quality": "high",
        "audioBitrate": "192k",
    }
    with CLIP.open("rb") as handle:
        created = requests.post(
            f"{base}/api/jobs",
            files={"file": ("clip.mp4", handle, "video/mp4")},
            data={"settings": json.dumps(settings)},
            timeout=300,
        )
    created.raise_for_status()
    job_id = created.json()["id"]
    print(f"  job {job_id} (computeScale={scale}) started", flush=True)

    samples: list[float] = []
    deadline = time.time() + 1800
    state: dict = {}
    while time.time() < deadline:
        state = requests.get(f"{base}/api/jobs/{job_id}", timeout=30).json()
        status = state.get("status")
        if status == "processing" and (state.get("processingFps") or 0) > 0:
            samples.append(float(state["processingFps"]))
        if status in ("done", "error", "cancelled"):
            break
        time.sleep(1.0)

    # Drop the first third: it includes model warm-up and cuDNN autotuning.
    steady = samples[len(samples) // 3:] or samples
    median = sorted(steady)[len(steady) // 2] if steady else 0.0
    result = {
        "id": job_id,
        "scale": scale,
        "status": state.get("status"),
        "error": state.get("error"),
        "elapsed": state.get("elapsedSeconds"),
        "median_fps": median,
        "enhanced": state.get("enhanced") or {},
        "device": state.get("device"),
    }
    try:
        requests.delete(f"{base}/api/jobs/{job_id}", timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE, help="API root, e.g. http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ensure_dirs()
    make_clip()
    print(f"clip: {CLIP} ({CLIP.stat().st_size} bytes)", flush=True)

    results = [run_job(base, scale) for scale in (1.0, 0.5)]

    print(f"\n=== {TARGET_W}x{TARGET_H} @ {TARGET_FPS} fps ===")
    ok = True
    for r in results:
        enh = r["enhanced"]
        geom_ok = (enh.get("width"), enh.get("height")) == (TARGET_W, TARGET_H)
        fps_ok = abs((enh.get("fps") or 0) - TARGET_FPS) < 0.5
        real_ok = (enh.get("interpolatedFrames") or 0) > 0
        ok = ok and r["status"] == "done" and geom_ok and fps_ok and real_ok
        print(
            f"  scale={r['scale']:<4} status={r['status']:<7} elapsed={r['elapsed']!s:>7}s "
            f"median={r['median_fps']:5.2f} interp/s  out={enh.get('width')}x{enh.get('height')}"
            f"@{enh.get('fps')} interpolated={enh.get('interpolatedFrames')} device={r['device']}"
            + (f"  ERROR={r['error']}" if r["error"] else "")
        )
        print(f"           geometry_ok={geom_ok} fps_ok={fps_ok} real_frames={real_ok}")

    full, fast = results[0], results[1]
    if full["median_fps"] and fast["median_fps"]:
        print(f"\n  speed-up (Fastest vs Full): {fast['median_fps'] / full['median_fps']:.2f}x")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
