"""FFmpeg helpers: capability detection, filter chains and encoder arguments."""
from __future__ import annotations

import functools
import math
import subprocess
from dataclasses import dataclass

from .config import FFMPEG, NVENC_PRESET, NVENC_TUNE, X264_PRESET
from . import subproc


class FfmpegError(Exception):
    pass


@dataclass
class EncoderChoice:
    name: str
    kind: str  # "nvenc" | "x264" | "x265"
    hardcoded_gpu: bool

    @property
    def label(self) -> str:
        return {
            "h264_nvenc": "H.264 (NVENC GPU)",
            "hevc_nvenc": "H.265 (NVENC GPU)",
            "libx264": "H.264 (CPU)",
            "libx265": "H.265 (CPU)",
        }.get(self.name, self.name)


@functools.lru_cache(maxsize=1)
def _available_encoders() -> set[str]:
    try:
        proc = subproc.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return names


def nvenc_actually_usable(encoder: str) -> bool:
    """NVENC can be listed but unavailable (old GPU, driver issue). Test with a dummy encode."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1:r=30",
        "-frames:v", "2", "-c:v", encoder, "-f", "null", "-",
    ]
    try:
        proc = subproc.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@functools.lru_cache(maxsize=1)
def detect_capabilities() -> dict:
    encoders = _available_encoders()
    caps = {
        "ffmpegFound": bool(encoders),
        "h264Nvenc": "h264_nvenc" in encoders,
        "hevcNvenc": "hevc_nvenc" in encoders,
        "libx264": "libx264" in encoders,
        "libx265": "libx265" in encoders,
        "nvencUsable": False,
    }
    if caps["h264Nvenc"]:
        caps["nvencUsable"] = nvenc_actually_usable("h264_nvenc")
    if not caps["h264Nvenc"]:
        caps["h264Nvenc"] = False
    return caps


def choose_encoder(codec: str, prefer_gpu: bool = True) -> EncoderChoice:
    """Pick the best available encoder for the requested codec."""
    caps = detect_capabilities()
    if not caps["ffmpegFound"]:
        raise FfmpegError(
            "FFmpeg was not found. Install it and make sure both ffmpeg and ffprobe are on PATH, "
            "or set AVE_FFMPEG / AVE_FFPROBE."
        )
    codec = codec.lower()
    if codec in ("h265", "hevc"):
        if prefer_gpu and caps["hevcNvenc"] and caps["nvencUsable"]:
            return EncoderChoice("hevc_nvenc", "nvenc", True)
        if caps["libx265"]:
            return EncoderChoice("libx265", "x265", False)
        if caps["hevcNvenc"]:
            return EncoderChoice("hevc_nvenc", "nvenc", True)
        raise FfmpegError("No H.265 encoder available in this FFmpeg build.")
    if prefer_gpu and caps["h264Nvenc"] and caps["nvencUsable"]:
        return EncoderChoice("h264_nvenc", "nvenc", True)
    if caps["libx264"]:
        return EncoderChoice("libx264", "x264", False)
    if caps["h264Nvenc"]:
        return EncoderChoice("h264_nvenc", "nvenc", True)
    raise FfmpegError("No H.264 encoder available in this FFmpeg build.")


def build_decode_command(
    input_path: str,
    *,
    target_w: int,
    target_h: int,
    fit_mode: str,
    fps_mode: str = "passthrough",
    source_w: int | None = None,
    source_h: int | None = None,
) -> list[str]:
    """Decode video to raw RGB24 frames on stdout, applying scale/crop/pad.

    Every frame written to stdout is exactly target_w x target_h, because the pipeline
    reads fixed-size frames. fit_mode decides how the source is fitted into that box:

    - "cover":   fill the box and crop the overflow.
    - "contain": fit inside the box and letterbox with black bars.
    - "original": keep the whole frame. target_w/target_h are normally derived from the
      source size, so no scaling happens; but when the AI renders at a smaller "compute
      size" (Faster/Fastest) or the source has odd dimensions, we must still scale down
      to the box, otherwise the fixed-size reads would drift out of sync.
    """
    chain: list[str] = []
    if fit_mode == "cover":
        chain.append(f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase:flags=bicubic")
        chain.append(f"crop={target_w}:{target_h}")
    elif fit_mode == "contain":
        chain.append(f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=bicubic")
        chain.append(f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black")
    elif (source_w, source_h) != (target_w, target_h):
        # "original" fit: only add a scaler when the frame does not already match the box.
        chain.append(f"scale={target_w}:{target_h}:flags=bicubic")
    chain.append("format=rgb24")

    return [
        FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", input_path,
        "-map", "0:v:0",
        "-vf", ",".join(chain),
        "-fps_mode", fps_mode,
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]


def build_encode_command(
    *,
    output_path: str,
    width: int,
    height: int,
    output_fps: float,
    out_width: int | None = None,
    out_height: int | None = None,
    encoder: EncoderChoice,
    audio_source: str | None,
    quality_cq: int | None,
    video_bitrate: str | None,
    audio_bitrate: str,
    audio_speed: float = 1.0,
    include_audio: bool = True,
    extra_audio_filters: list[str] | None = None,
) -> list[str]:
    """Encode raw RGB24 frames from stdin, muxing audio from the original file.

    width/height describe the frames arriving on stdin; out_width/out_height (defaults
    equal to width/height) are the encoded canvas. When they differ the frames are
    upscaled with lanczos — that is how "Faster"/"Fastest" processing keeps the full
    output size while the AI renders fewer pixels.
    """
    cmd: list[str] = [
        FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-framerate", f"{output_fps:.6f}",
        "-i", "-",
    ]

    has_audio = bool(audio_source) and include_audio
    if has_audio:
        cmd += ["-i", audio_source]

    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0?"]

    cmd += ["-c:v", encoder.name]

    target_w = out_width or width
    target_h = out_height or height
    if (target_w, target_h) != (width, height):
        cmd += ["-vf", f"scale={target_w}:{target_h}:flags=lanczos"]

    if encoder.kind == "nvenc":
        cmd += [
            "-preset", NVENC_PRESET,
            "-tune", NVENC_TUNE,
            "-rc", "vbr",
            "-spatial-aq", "1",
            "-temporal-aq", "1",
            "-profile:v", "high" if encoder.name == "h264_nvenc" else "main",
        ]
        if video_bitrate:
            cmd += ["-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", _double(video_bitrate)]
        else:
            cmd += ["-cq", str(quality_cq or 19), "-b:v", "0"]
    elif encoder.kind == "x264":
        # Do not pin an x264 level: the output size/FPS can exceed level 4.2
        # (e.g. 1080x1920 at 120 fps), and libx264 then refuses or silently
        # lowers quality to satisfy the cap. The encoder chooses a safe level.
        cmd += ["-preset", X264_PRESET, "-profile:v", "high"]
        if video_bitrate:
            cmd += ["-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", _double(video_bitrate)]
        else:
            cmd += ["-crf", str(quality_cq or 19)]
    else:  # x265
        cmd += ["-preset", "medium", "-tag:v", "hvc1"]
        if video_bitrate:
            cmd += ["-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", _double(video_bitrate)]
        else:
            cmd += ["-crf", str(quality_cq or 20)]

    cmd += [
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
    ]

    if has_audio:
        filters = list(extra_audio_filters or [])
        if abs(audio_speed - 1.0) > 1e-6:
            filters.extend(_atempo_filters(audio_speed))
        # Pad short audio with silence. Without this, "-shortest" stops at the
        # end of the audio track and can truncate the last video frames (most
        # visible in slow motion, where audio is time-stretched).
        filters.append("apad")
        cmd += ["-af", ",".join(filters)]
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000"]

    cmd += [
        "-movflags", "+faststart",
        "-fps_mode", "cfr",
    ]
    if has_audio:
        cmd += ["-shortest"]
    cmd += ["-f", "mp4", output_path]
    return cmd


def _atempo_filters(speed: float) -> list[str]:
    """Split a playback speed into chained ``atempo`` filters.

    FFmpeg's ``atempo`` filter accepts roughly 0.5–2.0 per instance, so 0.25x
    (slow motion) must be expressed as ``atempo=0.5,atempo=0.5``. Passing
    ``atempo=0.25`` directly makes FFmpeg reject the filter and fail the job.
    """
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(speed) or speed <= 0:
        return []
    if abs(speed - 1.0) < 1e-9:
        return []

    factors: list[float] = []
    remaining = speed
    while remaining < 0.5 - 1e-9:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    factors.append(min(2.0, max(0.5, remaining)))
    return [f"atempo={factor:.6f}" for factor in factors]


def _double(bitrate: str) -> str:
    try:
        if bitrate.lower().endswith("k"):
            return f"{int(bitrate[:-1]) * 2}k"
        if bitrate.lower().endswith("m"):
            return f"{int(float(bitrate[:-1]) * 2)}M"
        return f"{int(bitrate) * 2}"
    except (TypeError, ValueError):
        return bitrate
