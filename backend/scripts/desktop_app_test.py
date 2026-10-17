"""End-to-end check of the desktop app (packaged exe OR ``python -m app.desktop``).

Proves the frozen bundle works: bundled FFmpeg, bundled CUDA/cuDNN DLLs (RIFE on the
GPU), the bundled RIFE model, the LOCALAPPDATA storage and the full export pipeline.

    # packaged app (default port)
    .\\.venv\\Scripts\\python.exe scripts\\desktop_app_test.py

    # a source-mode launcher / any other base URL
    .\\.venv\\Scripts\\python.exe scripts\\desktop_app_test.py --base http://127.0.0.1:8123

Two short jobs are run on a generated clip:

  1. the default path (preset/fit "original", full detail)                     24 -> 60 fps
  2. "original" fit at the *Fastest* processing detail (computeScale 0.5)      24 -> 60 fps

Job 2 is the regression case: when the AI renders a smaller box than the source, the
decoder must still rescale to that box. If it does not, the frame reads drift out of
sync, the video runs far longer than the audio and FFmpeg's ``-shortest`` ends the
encoder early (a baffling "Broken pipe" error, with an empty FFmpeg stderr).

Exit code 0 means every check passed. Requires ``requests`` and FFmpeg/FFprobe on PATH
(only to generate and inspect the test clip).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

WORK = Path(tempfile.gettempdir()) / "ave_desktop_app_test"
WORK.mkdir(parents=True, exist_ok=True)
CLIP = WORK / "clip_320x240_24fps_2s.mp4"
OUT = WORK / "enhanced.mp4"

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures += 1


def probe(path: Path, count_frames: bool = False) -> dict:
    cmd = ["ffprobe", "-v", "error"]
    if count_frames:
        cmd.append("-count_frames")
    cmd += [
        "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames",
        "-show_entries", "format=duration,size",
        "-of", "json", str(path),
    ]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return json.loads(done.stdout or "{}")


def make_clip() -> None:
    if CLIP.exists():
        return
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", str(CLIP),
        ],
        check=True,
        timeout=300,
    )


def run_job(base: str, settings: dict) -> tuple[dict, dict]:
    with CLIP.open("rb") as handle:
        created = requests.post(
            f"{base}/api/jobs",
            files={"file": ("clip.mp4", handle, "video/mp4")},
            data={"settings": json.dumps(settings)},
            timeout=300,
        ).json()
    job_id = created["id"]

    state: dict = {}
    deadline = time.time() + 1800
    while time.time() < deadline:
        state = requests.get(f"{base}/api/jobs/{job_id}", timeout=30).json()
        if state.get("status") in ("done", "error", "cancelled"):
            break
        time.sleep(1.0)

    if state.get("status") != "done":
        return state, {}

    with requests.get(f"{base}/api/jobs/{job_id}/download", stream=True, timeout=300) as response:
        response.raise_for_status()
        OUT.write_bytes(response.content)
    try:
        requests.delete(f"{base}/api/jobs/{job_id}", timeout=120)
    except requests.RequestException:
        pass
    return state, probe(OUT, count_frames=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8123", help="app base URL")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print("0) generate the test clip")
    make_clip()
    src = probe(CLIP, count_frames=True)
    src_v = src["streams"][0]
    src_frames = int(src_v.get("nb_read_frames") or 0)
    src_dur = float(src["format"]["duration"])
    print(
        f"   source: {src_v['width']}x{src_v['height']} {src_v['avg_frame_rate']} "
        f"{src_frames} frames, {src_dur:.2f}s"
    )

    print("1) app metadata")
    health = requests.get(f"{base}/api/health", timeout=30).json()
    check("health responds", health.get("status") == "ok", str(health))
    system = requests.get(f"{base}/api/system", timeout=60).json()
    model = requests.get(f"{base}/api/system/model", timeout=60).json()
    ffmpeg_path = str(system.get("ffmpegPath") or "")
    if "_internal" in ffmpeg_path:
        check("ffmpeg is the bundled one", "AI Video Enhancer" in ffmpeg_path, ffmpeg_path)
    else:
        print(f"   (source mode) ffmpeg={ffmpeg_path}")
    check("RIFE model loaded", bool(model.get("loaded")), str(model.get("device")))
    check("RIFE running on CUDA", model.get("device") == "CUDA (GPU)", str(model.get("device")))
    check("NVENC available", bool(system.get("nvencAvailable")), str(system.get("h264Encoder")))
    print(f"   gpu={system.get('gpuName')} vramFree={system.get('gpuVramFreeMb')}MB")

    cases = [
        (
            "2) default path: preset=original, fit=original, full detail",
            {"targetFps": 60, "preset": "original", "fitMode": "original", "codec": "h264",
             "quality": "high", "audioBitrate": "192k", "slowMotion": False, "computeScale": 1.0},
            120,
        ),
        (
            "3) regression: fit=original at 50% processing detail (computeScale 0.5)",
            {"targetFps": 60, "preset": "original", "fitMode": "original", "codec": "h264",
             "quality": "high", "audioBitrate": "192k", "slowMotion": False, "computeScale": 0.5},
            120,
        ),
    ]

    for title, settings, expected_frames in cases:
        print(title)
        state, out = run_job(base, settings)
        check("job finished", state.get("status") == "done",
              f"{state.get('status')} {state.get('error') or ''}")
        if state.get("status") != "done":
            continue
        print(f"   enhanced: {json.dumps(state.get('enhanced'))[:280]}")
        streams = {s["codec_type"]: s for s in out.get("streams", [])}
        video = streams.get("video", {})
        audio = streams.get("audio")
        out_frames = int(video.get("nb_read_frames") or 0)
        out_dur = float(out["format"]["duration"])
        check("resolution preserved",
              (video.get("width"), video.get("height")) == (src_v["width"], src_v["height"]),
              f"{video.get('width')}x{video.get('height')}")
        check("fps is 60", video.get("avg_frame_rate") == "60/1", str(video.get("avg_frame_rate")))
        check("frame count is the interpolated count", abs(out_frames - expected_frames) <= 3,
              f"{src_frames} -> {out_frames} (expected ~{expected_frames})")
        check("duration preserved (not truncated by -shortest)",
              abs(out_dur - src_dur) <= max(0.15, src_dur * 0.1), f"{src_dur:.2f}s -> {out_dur:.2f}s")
        check("audio muxed", audio is not None,
              str(audio.get("codec_name") if audio else None))

    print()
    if failures:
        print(f"FAILED: {failures} check(s)")
        return 1
    print("All desktop-app checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
