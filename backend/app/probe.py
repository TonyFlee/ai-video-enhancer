"""FFprobe wrapper and video metadata helpers."""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .config import ALLOWED_VIDEO_CODECS, FFPROBE
from . import subproc


class ProbeError(Exception):
    """Raised when a file cannot be probed or is not a usable video."""


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    duration: float
    size_bytes: int
    video_codec: str
    pix_fmt: str | None
    has_audio: bool
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None
    bitrate: int | None
    nb_frames: int | None
    container: str
    rotation: int = 0

    @property
    def duration_seconds(self) -> float:
        return self.duration

    def as_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 4),
            "duration": round(self.duration, 3),
            "sizeBytes": self.size_bytes,
            "videoCodec": self.video_codec,
            "pixFmt": self.pix_fmt,
            "hasAudio": self.has_audio,
            "audioCodec": self.audio_codec,
            "audioChannels": self.audio_channels,
            "audioSampleRate": self.audio_sample_rate,
            "bitrate": self.bitrate,
            "nbFrames": self.nb_frames,
            "container": self.container,
        }


def _safe_int(value, default: int = 0) -> int:
    """int() that tolerates ffprobe's "N/A"/empty/malformed strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _parse_fps(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    try:
        value = float(Fraction(rate))
    except (ZeroDivisionError, ValueError):
        return 0.0
    # Guard against corrupt metadata such as "999999/1": it would create a
    # nonsensical frame plan and overflow the job's frame counters.
    if not math.isfinite(value) or value <= 0 or value > 1000:
        return 0.0
    return value


def _parse_rotation(stream: dict) -> int:
    tags = stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(raw) % 360
        except (TypeError, ValueError):
            pass
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(side["rotation"]) % 360
            except (TypeError, ValueError):
                continue
    return 0


def probe_video(path: str) -> VideoInfo:
    """Run ffprobe and validate that the file is a supported video."""
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        proc = subproc.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe was not found on PATH. Install FFmpeg and restart.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("Timed out while reading video metadata (file may be corrupted).") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        message = detail[-1] if detail else "Unknown ffprobe error"
        raise ProbeError(f"Unsupported or corrupted video: {message}")

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("Could not parse video metadata.") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("No video stream found in this file.")

    codec = (video.get("codec_name") or "").lower()
    if codec and codec not in ALLOWED_VIDEO_CODECS:
        raise ProbeError(f"Video codec '{codec}' is not supported. Try H.264/H.265/VP9/AV1 input.")

    duration = 0.0
    for candidate in (video.get("duration"), fmt.get("duration")):
        duration = _positive_float(candidate)
        if duration > 0:
            break

    fps = _parse_fps(video.get("avg_frame_rate"))
    if fps <= 0:
        fps = _parse_fps(video.get("r_frame_rate"))
    if fps <= 0:
        raise ProbeError("Could not determine the frame rate of this video.")

    width = _safe_int(video.get("width"))
    height = _safe_int(video.get("height"))
    if width <= 0 or height <= 0:
        raise ProbeError("Invalid video dimensions.")
    if width > 16384 or height > 16384:
        raise ProbeError(f"Video dimensions {width}x{height} are not supported.")

    rotation = _parse_rotation(video)
    if rotation in (90, 270):
        width, height = height, width

    parsed_frames = _safe_int(video.get("nb_frames"), 0)
    nb_frames = parsed_frames if parsed_frames > 0 else None

    # Some containers omit format duration (or report N/A). Derive it from the
    # frame count when possible, and fail with a clear message otherwise instead
    # of silently pretending the clip is zero seconds long.
    if duration <= 0 and nb_frames:
        duration = nb_frames / fps
    if duration <= 0:
        raise ProbeError("Could not determine the duration of this video (metadata is missing).")
    if nb_frames is None:
        nb_frames = max(1, int(round(duration * fps)))

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    audio_channels = _safe_int((audio or {}).get("channels"), 0) or None
    audio_sample_rate = _safe_int((audio or {}).get("sample_rate"), 0) or None

    bitrate = _safe_int(fmt.get("bit_rate"), 0) or _safe_int(video.get("bit_rate"), 0) or None
    size_bytes = _safe_int(fmt.get("size"), 0)
    if size_bytes <= 0:
        try:
            size_bytes = Path(path).stat().st_size
        except OSError:
            size_bytes = 0

    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        size_bytes=size_bytes,
        video_codec=codec,
        pix_fmt=video.get("pix_fmt"),
        has_audio=audio is not None,
        audio_codec=(audio or {}).get("codec_name"),
        audio_channels=audio_channels,
        audio_sample_rate=audio_sample_rate,
        bitrate=bitrate,
        nb_frames=nb_frames,
        container=(fmt.get("format_name") or "").split(",")[0],
        rotation=rotation,
    )
