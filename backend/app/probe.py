"""FFprobe wrapper and video metadata helpers."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction

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


def _parse_fps(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    try:
        return float(Fraction(rate))
    except (ZeroDivisionError, ValueError):
        return 0.0


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
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    fps = _parse_fps(video.get("avg_frame_rate"))
    if fps <= 0:
        fps = _parse_fps(video.get("r_frame_rate"))
    if fps <= 0:
        raise ProbeError("Could not determine the frame rate of this video.")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProbeError("Invalid video dimensions.")

    rotation = _parse_rotation(video)
    if rotation in (90, 270):
        width, height = height, width

    nb_frames = None
    try:
        nb_frames = int(video["nb_frames"])
    except (KeyError, TypeError, ValueError):
        nb_frames = None
    if nb_frames is None and duration > 0:
        nb_frames = int(round(duration * fps))

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = duration if duration > 0 else 0.0

    bitrate = None
    try:
        bitrate = int(fmt.get("bit_rate"))
    except (TypeError, ValueError):
        bitrate = None

    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        size_bytes=int(fmt.get("size") or 0),
        video_codec=codec,
        pix_fmt=video.get("pix_fmt"),
        has_audio=audio is not None,
        audio_codec=(audio or {}).get("codec_name"),
        audio_channels=(audio or {}).get("channels"),
        audio_sample_rate=int((audio or {}).get("sample_rate") or 0) or None,
        bitrate=bitrate,
        nb_frames=nb_frames,
        container=(fmt.get("format_name") or "").split(",")[0],
        rotation=rotation,
    )
