"""Pydantic request/response models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

PresetName = Literal["tiktok", "reels", "shorts", "vertical_1080p", "custom", "original"]
FitMode = Literal["cover", "contain", "original"]
CodecName = Literal["h264", "h265"]
QualityName = Literal["high", "balanced", "fast"]

PRESET_DIMENSIONS: dict[str, tuple[int, int] | None] = {
    "tiktok": (1080, 1920),
    "reels": (1080, 1920),
    "shorts": (1080, 1920),
    "vertical_1080p": (1080, 1920),
    # "custom" has no fixed size: custom_width/custom_height supply it.
    "custom": None,
    "original": None,
}

# Recommended max bitrates (Mbps) per preset at 60fps, used for the UI defaults.
PRESET_BITRATE_HINT: dict[str, float] = {
    "tiktok": 12.0,
    "reels": 12.0,
    "shorts": 14.0,
    "vertical_1080p": 12.0,
    "custom": 14.0,
    "original": 16.0,
}

QUALITY_CQ: dict[str, int] = {"high": 17, "balanced": 20, "fast": 24}


class JobSettings(BaseModel):
    target_fps: int = Field(default=60, ge=24, le=240, alias="targetFps")
    slow_motion: bool = Field(default=False, alias="slowMotion")
    slow_motion_speed: float = Field(default=0.5, gt=0.05, le=1.0, alias="slowMotionSpeed")
    preset: PresetName = "tiktok"
    fit_mode: FitMode = Field(default="cover", alias="fitMode")
    codec: CodecName = "h264"
    quality: QualityName = "high"
    video_bitrate_mbps: float | None = Field(default=None, ge=0.5, le=100.0, alias="videoBitrateMbps")
    audio_bitrate: str = Field(default="192k", alias="audioBitrate")
    prefer_gpu: bool = Field(default=True, alias="preferGpu")
    custom_width: int | None = Field(default=None, ge=64, le=4096, alias="customWidth")
    custom_height: int | None = Field(default=None, ge=64, le=4096, alias="customHeight")
    # Fraction of the output resolution the AI actually renders; the result is upscaled
    # by the encoder. 0.5 -> roughly 4x fewer pixels to interpolate (much faster).
    compute_scale: float = Field(default=1.0, ge=0.5, le=1.0, alias="computeScale")

    model_config = {"populate_by_name": True}

    @field_validator("target_fps")
    @classmethod
    def _allowed_fps(cls, value: int) -> int:
        if value not in (30, 60, 120, 240):
            raise ValueError("targetFps must be one of 30, 60, 120, 240")
        return value

    @field_validator("audio_bitrate")
    @classmethod
    def _allowed_audio(cls, value: str) -> str:
        allowed = {"96k", "128k", "160k", "192k", "256k", "320k"}
        if value not in allowed:
            raise ValueError(f"audioBitrate must be one of {sorted(allowed)}")
        return value

    @field_validator("slow_motion_speed")
    @classmethod
    def _allowed_speed(cls, value: float) -> float:
        allowed = (0.25, 0.5, 0.75, 1.0)
        if not any(abs(value - a) < 1e-6 for a in allowed):
            raise ValueError("slowMotionSpeed must be 0.25, 0.5, 0.75 or 1.0")
        return value

    @field_validator("compute_scale")
    @classmethod
    def _allowed_compute_scale(cls, value: float) -> float:
        allowed = (0.5, 0.75, 1.0)
        if not any(abs(value - a) < 1e-6 for a in allowed):
            raise ValueError("computeScale must be 0.5, 0.75 or 1.0")
        return value


class EnhancedInfo(BaseModel):
    width: int
    height: int
    fps: float
    duration: float
    sizeBytes: int
    videoCodec: str
    encoder: str
    encoderKind: str
    bitrate: int | None = None
    interpolatedFrames: int = 0
    interpolationFactor: float = 1.0


class JobState(BaseModel):
    id: str
    status: Literal["queued", "validating", "processing", "cancelling", "finalizing", "done", "error", "cancelled"]
    progress: float = 0.0
    stage: str = "queued"
    message: str = ""
    framesProcessed: int = 0
    totalFrames: int = 0
    processingFps: float = 0.0
    etaSeconds: float | None = None
    elapsedSeconds: float = 0.0
    error: str | None = None
    settings: JobSettings | None = None
    original: dict | None = None
    enhanced: EnhancedInfo | None = None
    device: str | None = None
    createdAt: float = 0.0
    updatedAt: float = 0.0


class SystemInfo(BaseModel):
    ffmpegAvailable: bool
    ffmpegPath: str | None = None
    rifeModelAvailable: bool
    rifeDevice: str | None = None
    rifeProviders: list[str] = []
    rifeTiling: bool = False
    nvencAvailable: bool = False
    h264Encoder: str | None = None
    h265Encoder: str | None = None
    gpuName: str | None = None
    cudaAvailable: bool = False
    gpuVramTotalMb: int | None = None
    gpuVramFreeMb: int | None = None
    freeDiskBytes: int = 0
    limits: dict = {}
    presets: dict = {}
