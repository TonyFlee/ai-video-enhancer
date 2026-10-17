"""Exercise the FastAPI surface end to end against a running uvicorn server.

Start the server first:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Then:
    .\\.venv\\Scripts\\python.exe scripts\\api_test.py

Covers: health/system, upload + validation errors, job creation, progress polling,
range requests on the original and the enhanced preview, download, cancel and delete.
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

from app.config import FFMPEG, OUTPUTS_DIR, TMP_DIR, ensure_dirs  # noqa: E402

BASE = "http://127.0.0.1:8000"
PASS, FAIL = "[PASS]", "[FAIL]"
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    print(f"  {PASS if ok else FAIL} {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures += 1


def make_clip(path: Path, seconds: int, size: str = "640x360", fps: int = 30) -> None:
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
    ]
    subprocess.run(cmd, check=True, timeout=300)


def wait_for_status(job_id: str, want: set[str], timeout: float = 900.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=30).json()
        if last.get("status") in want:
            return last
        if last.get("status") == "error":
            return last
        time.sleep(1.0)
    return last


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE,
                        help="API root, e.g. http://127.0.0.1:8000 or http://localhost:3000 (via the Next proxy)")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    print(f"target: {BASE}")

    ensure_dirs()
    print("1) server metadata")
    health = requests.get(f"{BASE}/api/health", timeout=30).json()
    check("health responds", health.get("status") == "ok", str(health))
    check("ffmpeg detected", bool(health.get("ffmpeg")), str(health))

    system = requests.get(f"{BASE}/api/system", timeout=60).json()
    check("system reports encoders", bool(system.get("h264Encoder")), str(system.get("h264Encoder")))
    check("system reports gpu", bool(system.get("gpuName")), str(system.get("gpuName")))

    model = requests.get(f"{BASE}/api/system/model", timeout=60).json()
    check("rife model loaded", bool(model.get("loaded")), str(model.get("device")))
    check("rife on CUDA", model.get("device") == "CUDA (GPU)", str(model.get("device")))

    print("2) validation")
    bad = requests.post(f"{BASE}/api/jobs", files={"file": ("notes.txt", b"hello")},
                        data={"settings": "{}"}, timeout=60)
    check("rejects unsupported extension (400)", bad.status_code == 400, f"got {bad.status_code}")

    source = TMP_DIR / "api_source.mp4"
    make_clip(source, seconds=1)
    source_60 = TMP_DIR / "api_source_60.mp4"
    make_clip(source_60, seconds=1, fps=60)
    with source_60.open("rb") as handle:
        bad_settings = requests.post(
            f"{BASE}/api/jobs",
            files={"file": ("clip60.mp4", handle, "video/mp4")},
            data={"settings": json.dumps({"targetFps": 30})},
            timeout=60,
        )
    # 60 fps source -> 30 fps target is a downgrade and must be rejected.
    check("rejects fps downgrade (400)", bad_settings.status_code == 400, f"got {bad_settings.status_code}")

    with source.open("rb") as handle:
        bad_fps = requests.post(
            f"{BASE}/api/jobs",
            files={"file": ("clip.mp4", handle, "video/mp4")},
            data={"settings": json.dumps({"targetFps": 15})},
            timeout=60,
        )
    check("rejects unsupported fps (422)", bad_fps.status_code == 422, f"got {bad_fps.status_code}")

    print("3) probe endpoint")
    with source.open("rb") as handle:
        probed = requests.post(f"{BASE}/api/probe",
                               files={"file": ("clip.mp4", handle, "video/mp4")}, timeout=120)
    check("probe returns metadata", probed.status_code == 200 and "original" in probed.json(),
          str(probed.status_code))

    print("4) create job and poll")
    settings = {"targetFps": 60, "preset": "original", "fitMode": "original", "codec": "h264",
                "quality": "high", "audioBitrate": "192k", "slowMotion": False}
    with source.open("rb") as handle:
        created = requests.post(
            f"{BASE}/api/jobs",
            files={"file": ("clip.mp4", handle, "video/mp4")},
            data={"settings": json.dumps(settings)},
            timeout=300,
        )
    check("job created (201/200)", created.status_code in (200, 201), f"got {created.status_code}")
    job = created.json()
    job_id = job["id"]
    check("job starts queued/validating/processing",
          job["status"] in ("queued", "validating", "processing"), job["status"])

    print("5) streaming the original while processing (range request)")
    rng = requests.get(f"{BASE}/api/jobs/{job_id}/original",
                       headers={"Range": "bytes=0-1023"}, timeout=60)
    check("original range request -> 206", rng.status_code == 206, f"got {rng.status_code}")
    check("range length is correct", len(rng.content) == 1024, f"{len(rng.content)} bytes")
    check("content-range header present", "bytes 0-1023/" in rng.headers.get("Content-Range", ""),
          rng.headers.get("Content-Range", ""))

    print("6) waiting for completion")
    final = wait_for_status(job_id, {"done"})
    check("job completed", final.get("status") == "done", str(final.get("status")) + " " + str(final.get("errorMessage") or ""))
    enhanced = final.get("enhanced") or {}
    check("enhanced fps is 60", abs(float(enhanced.get("fps") or 0) - 60) < 1.5, str(enhanced.get("fps")))
    check("progress reached 100%", (final.get("progress") or 0) >= 99, str(final.get("progress")))

    print("7) preview + download")
    prev = requests.get(f"{BASE}/api/jobs/{job_id}/preview", headers={"Range": "bytes=0-2047"}, timeout=60)
    check("preview range -> 206", prev.status_code == 206, f"got {prev.status_code}")

    dl = requests.get(f"{BASE}/api/jobs/{job_id}/download", timeout=300)
    check("download -> 200", dl.status_code == 200, f"got {dl.status_code}")
    check("download is an mp4", b"ftyp" in dl.content[:16],
          dl.headers.get("Content-Disposition", ""))
    check("download filename mentions enhanced",
          "enhanced" in dl.headers.get("Content-Disposition", ""),
          dl.headers.get("Content-Disposition", ""))

    print("8) cancel a running job")
    long_source = TMP_DIR / "api_long.mp4"
    make_clip(long_source, seconds=2, size="1280x720")
    with long_source.open("rb") as handle:
        created2 = requests.post(
            f"{BASE}/api/jobs",
            files={"file": ("long.mp4", handle, "video/mp4")},
            data={"settings": json.dumps({**settings, "targetFps": 120})},
            timeout=300,
        )
    job2 = created2.json()["id"]
    time.sleep(3.0)
    cancelled = requests.post(f"{BASE}/api/jobs/{job2}/cancel", timeout=60).json()
    check("cancel acknowledged", cancelled.get("status") == "cancelling",
          str(cancelled.get("status")))
    settled = wait_for_status(job2, {"cancelled"}, timeout=300)
    check("job ends cancelled", settled.get("status") == "cancelled", str(settled.get("status")))
    # A cancelled job must not leave a truncated MP4 behind (Windows keeps the handle
    # open until the killed encoder is reaped, so this used to fail with a 48-byte file).
    leftovers = [p.name for p in OUTPUTS_DIR.glob(f"{job2}*")]
    check("no partial output left after cancel", not leftovers, ", ".join(leftovers))

    print("8b) custom output size end to end")
    custom_settings = {**settings, "targetFps": 60, "preset": "custom",
                       "customWidth": 720, "customHeight": 1280, "fitMode": "cover"}
    with long_source.open("rb") as handle:
        created3 = requests.post(
            f"{BASE}/api/jobs",
            files={"file": ("custom.mp4", handle, "video/mp4")},
            data={"settings": json.dumps(custom_settings)},
            timeout=300,
        )
    check("custom job created", created3.status_code in (200, 201), f"got {created3.status_code}")
    job3 = created3.json()["id"]
    done3 = wait_for_status(job3, {"done"})
    check("custom job completed", done3.get("status") == "done",
          str(done3.get("status")) + " " + str(done3.get("errorMessage") or ""))
    enh3 = done3.get("enhanced") or {}
    check("output size is 720x1280", (enh3.get("width"), enh3.get("height")) == (720, 1280),
          f"{enh3.get('width')}x{enh3.get('height')}")
    check("custom job stays at 60 fps", abs(float(enh3.get("fps") or 0) - 60) < 1.5, str(enh3.get("fps")))
    requests.delete(f"{BASE}/api/jobs/{job3}", timeout=60)

    print("9) delete cleans up")
    deleted = requests.delete(f"{BASE}/api/jobs/{job_id}", timeout=60)
    check("delete -> 200", deleted.status_code == 200, f"got {deleted.status_code}")
    gone = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=60)
    check("deleted job is gone (404)", gone.status_code == 404, f"got {gone.status_code}")
    requests.delete(f"{BASE}/api/jobs/{job2}", timeout=60)

    print("10) unknown job")
    nf = requests.get(f"{BASE}/api/jobs/does-not-exist", timeout=30)
    check("unknown job -> 404", nf.status_code == 404, f"got {nf.status_code}")

    # Remove the clips this suite generated so storage/tmp does not accumulate them.
    for clip in (source, source_60, long_source):
        try:
            clip.unlink(missing_ok=True)
        except OSError:
            pass

    print(f"\n{'ALL API CHECKS PASSED' if _failures == 0 else f'{_failures} API CHECK(S) FAILED'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
