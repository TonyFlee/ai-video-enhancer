"""Unit tests for the pure logic: planning, geometry, FFmpeg arguments, timing.

Run with:
    cd backend
    .\\.venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import io
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ffmpeg import EncoderChoice, build_decode_command, build_encode_command  # noqa: E402
from app.pipeline import PairInterpolator, PipelineError, plan_factor, resolve_geometry, validate_settings  # noqa: E402
from app.probe import VideoInfo  # noqa: E402
from app.schemas import JobSettings  # noqa: E402


def make_info(width=1920, height=1080, fps=30.0, duration=4.0, nb_frames=None, has_audio=True) -> VideoInfo:
    return VideoInfo(
        path="sample.mp4",
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        size_bytes=1_000_000,
        video_codec="h264",
        pix_fmt="yuv420p",
        has_audio=has_audio,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        bitrate=8_000_000,
        nb_frames=nb_frames if nb_frames is not None else int(duration * fps),
        container="mov,mp4",
    )


# ------------------------------------------------------------------ planning
@pytest.mark.parametrize(
    "src_fps,target,slow,speed,expected",
    [
        (30.0, 60, False, 1.0, 2.0),
        (30.0, 120, False, 1.0, 4.0),
        (60.0, 120, False, 1.0, 2.0),
        (24.0, 60, False, 1.0, 2.5),
        (30.0, 60, True, 0.5, 4.0),
        (60.0, 60, True, 0.5, 2.0),
        (30.0, 30, True, 0.25, 4.0),
    ],
)
def test_plan_factor(src_fps, target, slow, speed, expected):
    settings = JobSettings(targetFps=target, slowMotion=slow, slowMotionSpeed=speed)
    assert plan_factor(make_info(fps=src_fps), settings) == pytest.approx(expected)


def test_validate_settings_rejects_downgrade():
    settings = JobSettings(targetFps=30)
    with pytest.raises(PipelineError):
        validate_settings(make_info(fps=60.0), settings)


def test_validate_settings_allows_slow_motion_downgrade():
    # 60 fps source at 0.5x with a 60 fps target legitimately produces 2x the frames.
    settings = JobSettings(targetFps=60, slowMotion=True, slowMotionSpeed=0.5)
    validate_settings(make_info(fps=60.0), settings)


def test_validate_settings_rejects_extreme_factor():
    settings = JobSettings(targetFps=240)
    with pytest.raises(PipelineError):
        validate_settings(make_info(fps=10.0, nb_frames=40), settings)


# ------------------------------------------------------------------ geometry
def test_geometry_tiktok_preset_is_vertical():
    width, height, fit = resolve_geometry(make_info(), JobSettings(preset="tiktok", fitMode="cover"))
    assert (width, height, fit) == (1080, 1920, "cover")


def test_geometry_original_preset_keeps_source_and_forces_even():
    info = make_info(width=1921, height=1081)
    width, height, fit = resolve_geometry(info, JobSettings(preset="original"))
    assert (width, height, fit) == (1920, 1080, "original")


def test_geometry_fit_mode_original_disables_scaling():
    info = make_info(width=1280, height=720)
    width, height, fit = resolve_geometry(info, JobSettings(preset="tiktok", fitMode="original"))
    assert (width, height, fit) == (1280, 720, "original")


def test_geometry_custom_dimensions():
    width, height, _ = resolve_geometry(make_info(), JobSettings(customWidth=1001, customHeight=777))
    assert (width, height) == (1000, 776)


def test_geometry_custom_preset_uses_requested_size():
    width, height, fit = resolve_geometry(
        make_info(),
        JobSettings(preset="custom", fitMode="contain", customWidth=720, customHeight=1280),
    )
    assert (width, height, fit) == (720, 1280, "contain")


def test_geometry_custom_preset_without_size_falls_back_to_source():
    info = make_info(width=1280, height=720)
    width, height, fit = resolve_geometry(info, JobSettings(preset="custom"))
    assert (width, height, fit) == (1280, 720, "original")


def test_custom_preset_accepted_and_round_trips_by_alias():
    settings = JobSettings.model_validate(
        {"targetFps": 60, "preset": "custom", "customWidth": 720, "customHeight": 1280}
    )
    assert settings.preset == "custom"
    payload = settings.model_dump(by_alias=True)
    assert payload["customWidth"] == 720
    assert payload["customHeight"] == 1280
    # The custom preset advertises no fixed frame size to the UI.
    from app.schemas import PRESET_BITRATE_HINT, PRESET_DIMENSIONS

    assert PRESET_DIMENSIONS["custom"] is None
    assert "custom" in PRESET_BITRATE_HINT


# ------------------------------------------------------- faster processing
def test_compute_scale_defaults_to_full_and_round_trips():
    assert JobSettings(targetFps=60).compute_scale == 1.0
    settings = JobSettings.model_validate({"targetFps": 60, "computeScale": 0.5})
    assert settings.compute_scale == 0.5
    assert settings.model_dump(by_alias=True)["computeScale"] == 0.5


def test_compute_scale_rejects_unsupported_values():
    import pytest

    for bad in (0.4, 0.9, 0.0, 1.5):
        with pytest.raises(Exception):
            JobSettings(targetFps=60, computeScale=bad)


def test_compute_frame_size_rounds_to_even():
    from app.pipeline import compute_frame_size

    assert compute_frame_size(1080, 1920, 1.0) == (1080, 1920)
    assert compute_frame_size(1080, 1920, 0.75) == (810, 1440)
    assert compute_frame_size(1080, 1920, 0.5) == (540, 960)
    # Odd results are snapped down so the H.264 encoder accepts them.
    assert compute_frame_size(1001, 777, 0.5) == (500, 388)
    # Tiny frames never collapse below the 32 px floor.
    assert compute_frame_size(40, 40, 0.5) == (32, 32)


def test_encode_command_upscales_when_compute_size_differs():
    encoder = EncoderChoice("h264_nvenc", "nvenc", True)
    cmd = build_encode_command(
        output_path="out.mp4", width=540, height=960, output_fps=60.0, encoder=encoder,
        audio_source="in.mp4", quality_cq=19, video_bitrate=None, audio_bitrate="192k",
        out_width=1080, out_height=1920,
    )
    # Raw frames arrive at the compute size, the encoder upscales to the real output.
    assert cmd[cmd.index("-s") + 1] == "540x960"
    assert "-vf" in cmd
    assert "scale=1080:1920:flags=lanczos" in cmd[cmd.index("-vf") + 1]


def test_encode_command_has_no_upscale_filter_at_full_detail():
    encoder = EncoderChoice("h264_nvenc", "nvenc", True)
    cmd = build_encode_command(
        output_path="out.mp4", width=1080, height=1920, output_fps=60.0, encoder=encoder,
        audio_source="in.mp4", quality_cq=19, video_bitrate=None, audio_bitrate="192k",
        out_width=1080, out_height=1920,
    )
    assert "-vf" not in cmd


# ------------------------------------------------------------- ffmpeg args
def test_decode_command_cover_crops():
    cmd = build_decode_command("in.mp4", target_w=1080, target_h=1920, fit_mode="cover")
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1080:1920" in vf
    assert "crop=1080:1920" in vf
    assert "format=rgb24" in vf
    assert cmd[-1] == "-"


def test_decode_command_contain_pads():
    cmd = build_decode_command("in.mp4", target_w=1080, target_h=1920, fit_mode="contain")
    vf = cmd[cmd.index("-vf") + 1]
    assert "pad=1080:1920" in vf


def test_decode_command_original_scales_to_smaller_compute_size():
    # "Fastest" (computeScale 0.5) with fit "original": the AI box is smaller than the
    # source, so the decoder MUST rescale. Otherwise the fixed-size frame reads drift out
    # of sync, the video ends up far longer than the audio and "-shortest" ends the
    # encoder early (surfacing as a confusing "Broken pipe").
    cmd = build_decode_command(
        "in.mp4", target_w=160, target_h=120, fit_mode="original",
        source_w=320, source_h=240,
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=160:120" in vf
    assert "format=rgb24" in vf


def test_decode_command_original_skips_scale_when_sizes_match():
    cmd = build_decode_command(
        "in.mp4", target_w=1280, target_h=720, fit_mode="original",
        source_w=1280, source_h=720,
    )
    assert "scale=" not in cmd[cmd.index("-vf") + 1]


def test_decode_command_original_scales_for_odd_source_dimensions():
    # resolve_geometry rounds odd dimensions down to even, so the decoder must rescale
    # 641x481 -> 640x480 to match the size the pipeline reads.
    cmd = build_decode_command(
        "in.mp4", target_w=640, target_h=480, fit_mode="original",
        source_w=641, source_h=481,
    )
    assert "scale=640:480" in cmd[cmd.index("-vf") + 1]


def test_nvenc_encode_args_use_cq_and_no_bitrate():
    encoder = EncoderChoice("h264_nvenc", "nvenc", True)
    cmd = build_encode_command(
        output_path="out.mp4", width=1080, height=1920, output_fps=60.0, encoder=encoder,
        audio_source="in.mp4", quality_cq=18, video_bitrate=None, audio_bitrate="192k",
    )
    assert "-cq" in cmd and cmd[cmd.index("-cq") + 1] == "18"
    assert cmd[cmd.index("-b:v") + 1] == "0"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-movflags" in cmd
    assert cmd[cmd.index("-framerate") + 1] == "60.000000"


def test_x264_encode_args_use_crf_and_bitrate_when_set():
    encoder = EncoderChoice("libx264", "x264", False)
    cmd = build_encode_command(
        output_path="out.mp4", width=1920, height=1080, output_fps=30.0, encoder=encoder,
        audio_source=None, quality_cq=20, video_bitrate="10M", audio_bitrate="128k",
    )
    assert "-crf" not in cmd
    assert cmd[cmd.index("-b:v") + 1] == "10M"
    assert cmd[cmd.index("-maxrate") + 1] == "10M"
    assert cmd[cmd.index("-bufsize") + 1] == "20M"
    assert "-c:a" not in cmd  # no audio source


def test_encode_args_time_stretch_audio_for_slow_motion():
    encoder = EncoderChoice("h264_nvenc", "nvenc", True)
    cmd = build_encode_command(
        output_path="out.mp4", width=1080, height=1920, output_fps=60.0, encoder=encoder,
        audio_source="in.mp4", quality_cq=19, video_bitrate=None, audio_bitrate="192k",
        audio_speed=0.5,
    )
    assert cmd[cmd.index("-af") + 1] == "atempo=0.500000"


# --------------------------------------------------- interpolation timing
class StubEngine:
    """Returns a constant grey frame; records timesteps and honours supports_timestep."""

    def __init__(self, supports_timestep: bool) -> None:
        self._supports = supports_timestep
        self.calls: list[float] = []

    @property
    def supports_timestep(self) -> bool:
        return self._supports

    def interpolate(self, a, b, t=None):
        self.calls.append(0.5 if t is None else t)
        return np.full_like(a, 128)


def test_pair_interpolator_uses_timestep_directly():
    engine = StubEngine(supports_timestep=True)
    pair = PairInterpolator(engine, np.zeros((8, 8, 3), np.uint8), np.zeros((8, 8, 3), np.uint8))
    pair.at(0.375)
    assert engine.calls == [0.375]


def test_pair_interpolator_bisects_when_timestep_missing():
    engine = StubEngine(supports_timestep=False)
    a = np.zeros((8, 8, 3), np.uint8)
    pair = PairInterpolator(engine, a, a)
    quarter = pair.at(0.25)
    # 0.25 (2/8) is reached by bisection: first the midpoint of [a, b], then the
    # midpoint of [a, midpoint]. Both are t=0.5 within their own sub-range.
    assert engine.calls == [0.5, 0.5]
    assert quarter.shape == a.shape
    # Endpoints never call the model.
    engine.calls.clear()
    pair.at(0.0)
    pair.at(1.0)
    assert engine.calls == []


def test_pair_interpolator_snaps_unsupported_phases():
    engine = StubEngine(supports_timestep=False)
    a = np.zeros((8, 8, 3), np.uint8)
    pair = PairInterpolator(engine, a, a)
    pair.at(0.37)  # snapped to 0.375 (3/8) -> three midpoint calls along the path
    assert all(abs(call - 0.5) < 1e-9 for call in engine.calls)
    assert len(engine.calls) == 3


@pytest.mark.parametrize(
    "factor,src_frames,expected_total,expected_interpolated",
    [
        (2.0, 5, 10, 4),
        (2.5, 4, 10, 6),
        (4.0, 3, 12, 6),
        (1.25, 4, 5, 3),
    ],
)
def test_output_frame_slot_mapping(factor, src_frames, expected_total, expected_interpolated):
    """Reproduce the pipeline's slot mapping without touching FFmpeg.

    The final `factor - 1` output slots have no following source frame to interpolate
    towards, so they legitimately repeat the last source frame to preserve duration.
    """
    total_out = round(src_frames * factor)
    emitted = 0
    interpolated = 0
    next_output = 0
    for src_index in range(src_frames - 1):
        while next_output < total_out:
            p = next_output / factor
            if p >= (src_index + 1) - 1e-9:
                break
            t = p - src_index
            if t < 1e-9:
                pass
            else:
                interpolated += 1
            next_output += 1
            emitted += 1
    # Flush remaining slots with the final source frame.
    while next_output < total_out:
        next_output += 1
        emitted += 1

    assert emitted == total_out
    assert total_out == expected_total
    assert interpolated == expected_interpolated


def test_no_duplicate_frames_for_integer_factor():
    """Every interior output slot sits strictly between two source frames."""
    factor = 3.0
    src_frames = 4
    total_out = int(round(src_frames * factor))
    timesteps = []
    next_output = 0
    for src_index in range(src_frames - 1):
        while next_output < total_out:
            p = next_output / factor
            if p >= (src_index + 1) - 1e-9:
                break
            t = p - src_index
            if t >= 1e-9:
                timesteps.append(t)
            next_output += 1
    assert all(0 < t < 1 for t in timesteps)
    # Three source-frame gaps x two new frames each; the remaining two slots hold the
    # final frame because there is no fourth source frame to interpolate towards.
    assert len(timesteps) == total_out - src_frames - int(factor - 1)
    assert timesteps == pytest.approx([1 / 3, 2 / 3] * 3)


# --------------------------------------------------- VRAM-aware tiling policy
class _EngineForTiling:
    """Bare attribute container exercising RifeEngine._auto_tile_for in isolation."""

    def __init__(self, free_mb, using_gpu=True):
        self._use_tiles = False
        self.using_gpu = using_gpu
        self._vram_limit_mb = None
        self._free_mb = free_mb


def test_auto_tile_enables_tiling_when_vram_is_tight(monkeypatch):
    import app.rife as rife

    monkeypatch.setattr(rife, "gpu_memory_info", lambda: {"total": 4096, "used": 3100, "free": 996})
    engine = _EngineForTiling(free_mb=996)
    rife.RifeEngine._auto_tile_for(engine, 1280, 720)
    assert engine._use_tiles is True


def test_auto_tile_stays_full_frame_with_plenty_of_vram(monkeypatch):
    import app.rife as rife

    monkeypatch.setattr(rife, "gpu_memory_info", lambda: {"total": 24576, "used": 2000, "free": 22576})
    engine = _EngineForTiling(free_mb=22576)
    rife.RifeEngine._auto_tile_for(engine, 1080, 1920)
    assert engine._use_tiles is False


def test_auto_tile_keeps_small_frames_full_frame(monkeypatch):
    import app.rife as rife

    monkeypatch.setattr(rife, "gpu_memory_info", lambda: {"total": 4096, "used": 3100, "free": 996})
    engine = _EngineForTiling(free_mb=996)
    rife.RifeEngine._auto_tile_for(engine, 640, 360)
    assert engine._use_tiles is False


def test_auto_tile_ignored_on_cpu(monkeypatch):
    import app.rife as rife

    monkeypatch.setattr(rife, "gpu_memory_info", lambda: {"total": 4096, "used": 3100, "free": 996})
    engine = _EngineForTiling(free_mb=996, using_gpu=False)
    rife.RifeEngine._auto_tile_for(engine, 1920, 1080)
    assert engine._use_tiles is False


# --------------------------------------------------------------- VRAM probing
def test_gpu_memory_info_parses_nvidia_smi(monkeypatch):
    import app.config as config

    class _Proc:
        returncode = 0
        stdout = "4096, 2662, 1274\n"

    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: _Proc())
    config._GPU_MEMO = None
    info = config.gpu_memory_info()
    assert info == {"total": 4096, "used": 2662, "free": 1274}


def test_gpu_memory_info_returns_none_without_nvidia_smi(monkeypatch):
    import app.config as config

    def boom(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(config.subprocess, "run", boom)
    config._GPU_MEMO = None
    assert config.gpu_memory_info() is None


# ------------------------------------------------- cancel cleanup (Windows locks)
def _job_with_output(tmp_path, name="partial.mp4"):
    from app.jobs import Job
    from app.schemas import JobSettings

    path = tmp_path / name
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)  # 48-byte partial MP4
    return Job(
        id="job1",
        settings=JobSettings(),
        upload_path=tmp_path / "source.mp4",
        output_path=path,
        state=None,
        source_name="source.mp4",
    )


def test_cleanup_output_removes_partial_file(tmp_path):
    from app.jobs import JobManager

    job = _job_with_output(tmp_path)
    JobManager()._cleanup_output(job)
    assert not job.output_path.exists()


def test_cleanup_output_retries_when_file_is_locked(tmp_path, monkeypatch):
    """Windows raises PermissionError while a handle is open; the helper must retry."""
    from app.jobs import JobManager

    job = _job_with_output(tmp_path)
    real_unlink = Path.unlink
    calls = {"n": 0}

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(32, "The process cannot access the file")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky)
    monkeypatch.setattr("app.jobs.time.sleep", lambda *_: None)
    JobManager()._cleanup_output(job)
    assert calls["n"] == 3
    assert not job.output_path.exists()


def test_cleanup_output_gives_up_quietly_on_persistent_lock(tmp_path, monkeypatch):
    from app.jobs import JobManager

    job = _job_with_output(tmp_path)

    def always_locked(self, *a, **k):
        raise PermissionError(32, "locked")

    monkeypatch.setattr(Path, "unlink", always_locked)
    monkeypatch.setattr("app.jobs.time.sleep", lambda *_: None)
    JobManager()._cleanup_output(job)  # must not raise
    assert job.output_path.exists()


def test_terminate_waits_for_process(tmp_path):
    import subprocess
    import sys

    from app.pipeline import _terminate

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    _terminate(proc)
    assert proc.poll() is not None  # reaped, not merely signalled


def test_cancel_queued_job_settles_without_the_gpu_slot(tmp_path):
    """A queued job must honour cancel immediately instead of waiting for the slot.

    Regression: the worker used to block in Semaphore.acquire(), so the UI stayed on
    "Cancelling..." for as long as the running job had left.
    """
    from app.jobs import JobManager

    manager = JobManager()
    upload = tmp_path / "queued.mp4"
    upload.write_bytes(b"\x00")

    # Simulate another job holding the single GPU slot.
    assert manager._process_semaphore.acquire(blocking=False)
    try:
        job_state = manager.create_job(
            "queuedcanceljob", JobSettings(targetFps=60), upload, "queued.mp4", {}
        )
        deadline = time.time() + 5
        while manager.get(job_state.id).state.status != "queued" and time.time() < deadline:
            time.sleep(0.05)
        assert manager.get(job_state.id).state.status == "queued"

        assert manager.cancel(job_state.id) is True
        deadline = time.time() + 5
        while manager.get(job_state.id).state.status != "cancelled" and time.time() < deadline:
            time.sleep(0.05)
        assert manager.get(job_state.id).state.status == "cancelled"

        manager.get(job_state.id).thread.join(timeout=5)
        # It never took the slot, so it must not hand a permit back on the way out.
        assert manager._process_semaphore.acquire(blocking=False) is False
    finally:
        manager._process_semaphore.release()


# ------------------------------------------------- subprocess / console hygiene
# The packaged desktop app is a *windowed* process with no console of its own. On
# Windows, every console-mode child it starts (ffmpeg, ffprobe, nvidia-smi) gets a
# brand new console window allocated for it unless CREATE_NO_WINDOW is passed -
# which is what made the app spray terminal windows on every /api/health poll.
APP_DIR = Path(__file__).resolve().parent.parent / "app"
FORBIDDEN_SPAWNS = re.compile(
    r"(?:subprocess\.(?:run|Popen|call|check_output|check_call)|os\.system|os\.popen)\s*\("
)


def test_children_never_open_console_windows():
    """No module may spawn a process directly - everything goes through subproc."""
    offenders = []
    for path in sorted(APP_DIR.glob("*.py")):
        if path.name == "subproc.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN_SPAWNS.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "these spawn processes without CREATE_NO_WINDOW, which pops a terminal "
        "window in the desktop app:\n" + "\n".join(offenders)
    )


def test_subproc_always_hides_the_console(monkeypatch):
    from app import subproc

    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    subproc.run(["ffmpeg", "-version"], capture_output=True)
    assert captured.get("capture_output") is True
    assert captured.get("creationflags") == subprocess.CREATE_NO_WINDOW

    captured.clear()
    subproc.popen(["ffprobe", "-version"], stdout=subprocess.PIPE)
    assert captured.get("creationflags") == subprocess.CREATE_NO_WINDOW

    # An explicit flag must still win (the pipeline already passed one).
    captured.clear()
    subproc.run(["ffmpeg", "-version"], creationflags=0)
    assert captured.get("creationflags") == 0


def test_ffmpeg_available_is_cached_across_health_polls(monkeypatch):
    """The UI polls /api/health; it must not respawn ffmpeg/ffprobe every time."""
    from app import config

    probed: list[str] = []

    def fake_works(binary: str) -> bool:
        probed.append(binary)
        return True

    config.ffmpeg_available.cache_clear()
    monkeypatch.setattr(config, "_binary_works", fake_works)
    try:
        for _ in range(5):
            assert config.ffmpeg_available() is True
        assert len(probed) == 2, f"expected one probe per binary, got {len(probed)}"
    finally:
        config.ffmpeg_available.cache_clear()


def test_gpu_name_is_cached_across_system_polls(monkeypatch):
    """nvidia-smi on every /api/system poll was one of the console-window sources."""
    from app import main as main_module

    calls: list[list[str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "GeForce GTX 1650\n"

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    main_module._gpu_name.cache_clear()
    try:
        assert main_module._gpu_name() == "GeForce GTX 1650"
        assert main_module._gpu_name() == "GeForce GTX 1650"
        assert len(calls) == 1, f"nvidia-smi ran {len(calls)} times for one cached value"
    finally:
        main_module._gpu_name.cache_clear()


# ------------------------------------------------------------- headless mode
def test_ave_no_window_runs_headless(monkeypatch):
    """AVE_NO_WINDOW=1 must keep the engine alive without opening a desktop window.

    Closing the window quits the app (and cancels any job still in flight), so
    automated end-to-end runs of the packaged build rely on this switch.
    """
    from app import desktop

    opened: list[str] = []
    waited: list[bool] = []

    class _Thread:
        def join(self, timeout=None):  # noqa: ARG002 - mirror threading.Thread
            return None

    class _Server:
        should_exit = False

    monkeypatch.setenv("AVE_NO_WINDOW", "1")
    monkeypatch.setenv("AVE_SERVE_UI", "1")  # main() setdefaults it; keep tests clean
    monkeypatch.setattr(desktop, "bootstrap_logging", lambda: Path("nul"))
    monkeypatch.setattr(desktop, "_port_is_ours", lambda port: False)
    monkeypatch.setattr(desktop, "_pick_port", lambda preferred: preferred)
    monkeypatch.setattr(desktop, "_start_server", lambda port: (_Server(), _Thread()))
    monkeypatch.setattr(desktop, "_healthy", lambda url, timeout=1.5: True)
    monkeypatch.setattr(desktop, "_open_window", lambda url: opened.append(url))
    monkeypatch.setattr(desktop, "_wait_for_interrupt", lambda: waited.append(True))

    assert desktop.main() == 0
    assert opened == [], "headless mode must not open a desktop window"
    assert waited == [True], "headless mode must keep the engine alive"


# ------------------------------------------------------------------ updates
class _FakeResponse:
    """Stand-in for urllib's response object: streamed reads + context manager."""

    def __init__(self, payload: bytes, headers: dict | None = None):
        self._buf = io.BytesIO(payload)
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_release(tag: str = "v9.9.9", body: str = "New stuff.\nSHA-256: {sha}\n") -> bytes:
    import hashlib
    import json

    payload = hashlib.sha256(b"installer-bytes").hexdigest()
    return json.dumps(
        {
            "tag_name": tag,
            "body": body.format(sha=payload),
            "published_at": "2026-10-19T00:00:00Z",
            "assets": [
                {
                    "name": f"AI-Video-Enhancer-Setup-{tag.lstrip('v')}.exe",
                    "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/setup.exe",
                    "size": 16,
                }
            ],
        }
    ).encode()


def test_semver_compare():
    from app.update import is_newer, parse_version

    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.10") == (1, 10, 0)
    assert parse_version("") == (0, 0, 0)
    assert is_newer("v1.0.1", "1.0.0") is True
    assert is_newer("v1.0.0", "1.0.0") is False
    assert is_newer("v0.9.9", "1.0.0") is False
    assert is_newer("v2.0", "1.9.9") is True


def test_update_check_parses_release_and_hides_failures(monkeypatch):
    from app import update

    monkeypatch.setattr(update, "_CHECK_CACHE", None)
    monkeypatch.setattr(update, "UPDATE_REPO", "example/app")

    # 1. A real newer release -> parsed, SHA line extracted and stripped from notes.
    monkeypatch.setattr(update.urlrequest, "urlopen", lambda req, timeout: _FakeResponse(_fake_release()))
    info = update.check_for_update(force=True)
    assert info is not None
    assert info.tag == "v9.9.9"
    assert info.version == "9.9.9"
    assert info.size_bytes == 16
    assert info.notes == "New stuff."
    assert info.sha256 and len(info.sha256) == 64

    # 2. Cached - the second call must not hit the network again.
    def _boom(req, timeout):
        raise AssertionError("cache miss: network was hit again")

    monkeypatch.setattr(update.urlrequest, "urlopen", _boom)
    assert update.check_for_update(force=False) is not None

    # 3. Same version as installed -> no update.
    monkeypatch.setattr(update, "_CHECK_CACHE", None)
    monkeypatch.setattr(
        update.urlrequest, "urlopen", lambda req, timeout: _FakeResponse(_fake_release(tag="v1.0.0"))
    )
    assert update.check_for_update(force=True) is None

    # 4. Offline / API down -> None, never an exception.
    def _offline(req, timeout):
        raise OSError("no network")

    monkeypatch.setattr(update.urlrequest, "urlopen", _offline)
    assert update.check_for_update(force=True) is None

    # 5. Release without an installer asset -> ignored.
    monkeypatch.setattr(update, "_CHECK_CACHE", None)
    import json

    payload = json.loads(_fake_release())
    payload["assets"] = []
    monkeypatch.setattr(
        update.urlrequest, "urlopen", lambda req, timeout: _FakeResponse(json.dumps(payload).encode())
    )
    assert update.check_for_update(force=True) is None


def test_update_download_verifies_sha256(monkeypatch, tmp_path):
    import hashlib

    from app import update

    content = b"installer-bytes"
    good_sha = hashlib.sha256(content).hexdigest()

    class _FakeUrlLib:
        """Duck-typed urllib.request: passthrough Request + canned response."""

        @staticmethod
        def Request(url, headers=None):  # noqa: N803 - mirrors urllib's signature
            return url

        @staticmethod
        def urlopen(req, timeout):
            return _FakeResponse(content, {"Content-Length": str(len(content))})

    monkeypatch.setattr(update, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(update, "urlrequest", _FakeUrlLib)

    info = update.UpdateInfo(
        tag="v9.9.9",
        version="9.9.9",
        url="https://github.com/x/y/releases/download/v9.9.9/setup.exe",
        size_bytes=len(content),
        notes="",
        published_at="",
        sha256=good_sha,
    )
    update._DOWNLOAD.__init__()  # reset module state between tests
    update.start_download(info)
    for _ in range(500):
        if update._DOWNLOAD.state != "downloading":
            break
        time.sleep(0.01)
    assert update._DOWNLOAD.state == "ready", update._DOWNLOAD.error
    assert update._DOWNLOAD.received == len(content)
    assert update._DOWNLOAD.sha256_ok is True
    installer = update.ready_installer()
    assert installer is not None and installer.read_bytes() == content

    # Wrong published hash -> the file is deleted and the state is an error.
    monkeypatch.setattr(update, "STORAGE_DIR", tmp_path / "bad")
    bad = update.UpdateInfo(**{**info.__dict__, "sha256": "0" * 64})
    update._DOWNLOAD.__init__()
    update.start_download(bad)
    for _ in range(500):
        if update._DOWNLOAD.state != "downloading":
            break
        time.sleep(0.01)
    assert update._DOWNLOAD.state == "error"
    assert update._DOWNLOAD.sha256_ok is False
    assert update.ready_installer() is None


def test_update_install_requires_a_ready_installer():
    from app import update

    update._DOWNLOAD.__init__()
    assert update.ready_installer() is None


