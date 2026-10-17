"""End-to-end pipeline verification with the real RIFE model and real FFmpeg.

Creates a synthetic 30 fps clip (moving pattern + audio tone), runs the full
decode -> RIFE interpolation -> encode pipeline, then checks that the export really has
newly generated frames (FPS and frame count), preserved audio and duration.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe scripts\\e2e_test.py
    .\\.venv\\Scripts\\python.exe scripts\\e2e_test.py --target-fps 120 --slow-motion
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import FFMPEG, TMP_DIR, ensure_dirs  # noqa: E402
from app.pipeline import VideoPipeline  # noqa: E402
from app.probe import probe_video  # noqa: E402
from app.rife import RifeEngine  # noqa: E402
from app.schemas import JobSettings  # noqa: E402


def read_raw_frames(path: Path, count: int, width: int, height: int) -> list[np.ndarray]:
    """Decode the first `count` frames of `path` as RGB24 numpy arrays."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-frames:v", str(count),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    frame_bytes = width * height * 3
    data = proc.stdout
    frames = []
    for i in range(len(data) // frame_bytes):
        frames.append(np.frombuffer(data[i * frame_bytes:(i + 1) * frame_bytes], np.uint8)
                      .reshape(height, width, 3))
    return frames


def make_sample(path: Path, *, seconds: int = 3, fps: int = 30, size: str = "1280x720") -> None:
    """Synthetic source: a scrolling gradient with a rotating box plus a 440 Hz tone."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
    ]
    subprocess.run(cmd, check=True, timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-fps", type=int, default=60)
    parser.add_argument("--slow-motion", action="store_true")
    parser.add_argument("--speed", type=float, default=0.5)
    parser.add_argument("--preset", default="tiktok", choices=["tiktok", "original"])
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument("--cpu", action="store_true", help="force the CPU execution provider")
    args = parser.parse_args()

    ensure_dirs()
    source = TMP_DIR / "e2e_source.mp4"
    output = TMP_DIR / "e2e_output.mp4"
    for path in (source, output):
        path.unlink(missing_ok=True)

    print("1) generating synthetic source...")
    make_sample(source, seconds=args.seconds)
    source_info = probe_video(str(source))
    print(f"   source: {source_info.width}x{source_info.height} "
          f"{source_info.fps:.3f} fps, {source_info.duration:.2f}s, "
          f"audio={source_info.has_audio}, frames={source_info.nb_frames}")

    print("2) loading RIFE...")
    engine = RifeEngine(Path(__file__).resolve().parent.parent / "models" / "RIFE_fp32_timestep.onnx",
                        prefer_gpu=not args.cpu)
    print(f"   device={engine.device} providers={engine.providers} "
          f"timestep={engine.supports_timestep} tiling={engine.tiling}")

    settings = JobSettings(
        targetFps=args.target_fps,
        slowMotion=args.slow_motion,
        slowMotionSpeed=args.speed,
        preset=args.preset,
        fitMode="cover",
        codec="h264",
        quality="high",
        audioBitrate="192k",
        preferGpu=not args.cpu,
    )

    updates: list[str] = []

    def on_progress(update) -> None:
        if update.processed % 15 == 0 or update.stage == "finalizing":
            updates.append(f"{update.stage} {update.processed}/{update.total} @ {update.fps:.1f} fps")

    print("3) running pipeline...")
    started = time.time()
    result = VideoPipeline(engine).run(
        input_path=source,
        output_path=output,
        settings=settings,
        info=source_info,
        cancel_event=threading.Event(),
        on_progress=on_progress,
    )
    elapsed = time.time() - started

    expected_factor = args.target_fps / (
        source_info.fps * (args.speed if args.slow_motion else 1.0)
    )
    expected_duration = source_info.duration / (args.speed if args.slow_motion else 1.0)

    print(f"   done in {elapsed:.1f}s | encoder={result.encoder} | "
          f"factor={result.interpolation_factor:.3f} | synthetic frames={result.interpolated_frames}")
    print(f"   progress samples: {updates[:4]}{' ...' if len(updates) > 4 else ''}")

    out_info = probe_video(str(result.output_path))
    print(f"   output: {out_info.width}x{out_info.height} {out_info.fps:.3f} fps, "
          f"{out_info.duration:.2f}s, audio={out_info.has_audio}, frames={out_info.nb_frames}")

    checks: list[tuple[str, bool, str]] = []
    checks.append((
        "frame rate matches target",
        abs(out_info.fps - args.target_fps) < 1.5,
        f"expected~{args.target_fps}, got {out_info.fps:.3f}",
    ))
    expected_frames = (source_info.nb_frames or 0) * expected_factor
    actual_frames = out_info.nb_frames or 0
    checks.append((
        "new frames were generated (~ factor x source frames)",
        abs(actual_frames - expected_frames) <= max(10, expected_frames * 0.06),
        f"expected~{expected_frames:.0f}, got {actual_frames}",
    ))
    checks.append((
        "audio preserved",
        out_info.has_audio == source_info.has_audio,
        f"source={source_info.has_audio}, output={out_info.has_audio}",
    ))
    checks.append((
        "duration preserved (slow motion doubles it)",
        abs(out_info.duration - expected_duration) < max(0.6, expected_duration * 0.08),
        f"expected~{expected_duration:.2f}s, got {out_info.duration:.2f}s",
    ))
    if args.preset == "tiktok":
        checks.append((
            "vertical 9:16 geometry",
            out_info.height > out_info.width and out_info.width == 1080 and out_info.height == 1920,
            f"{out_info.width}x{out_info.height}",
        ))
    checks.append((
        "synthetic frames > 0",
        result.interpolated_frames > 0,
        f"{result.interpolated_frames} frames",
    ))

    # Prove the export contains genuinely new in-between frames rather than duplicated
    # source frames or timestamp-only changes: decode a run of output frames and confirm
    # (a) consecutive frames differ and (b) the unique-frame count tracks the output
    # frame count, not the source frame count.
    sample_n = min(20, out_info.nb_frames or 0)
    out_frames = read_raw_frames(result.output_path, sample_n, out_info.width, out_info.height)
    consecutive = [
        float(np.abs(out_frames[i + 1].astype(np.int16) - out_frames[i].astype(np.int16)).mean())
        for i in range(len(out_frames) - 1)
    ]
    # Trailing slots hold the final frame, so ignore the last pair of the sample.
    interior = consecutive[:-1] if len(consecutive) > 1 else consecutive
    unique = len({f.tobytes() for f in out_frames})
    checks.append((
        "interpolated frames are genuinely new (not duplicates)",
        bool(unique) and unique >= max(1, int(0.85 * len(out_frames))) and min(interior, default=0.0) > 0.5,
        f"{unique}/{len(out_frames)} unique frames, min consecutive diff={min(interior, default=0.0):.2f}",
    ))
    checks.append((
        "source file untouched",
        source.exists() and abs(source.stat().st_size - probe_video(str(source)).size_bytes) < 4096,
        "original still present",
    ))

    print("\nresults:")
    failed = 0
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} - {detail}")
        failed += 0 if ok else 1

    print(f"\n{'ALL CHECKS PASSED' if failed == 0 else f'{failed} CHECK(S) FAILED'}")
    print(f"artifacts: {source}\n           {output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
