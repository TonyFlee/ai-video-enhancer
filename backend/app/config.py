"""Application configuration and paths."""
from __future__ import annotations

import functools
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import subproc

# Backend root directory (backend/)
BASE_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- env parsing
# A malformed environment variable must not stop the app from starting. These
# helpers fall back to the documented default instead of raising at import time.
def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    return int(
        round(
            _env_float(
                name,
                float(default),
                minimum=float(minimum),
                maximum=float(maximum) if maximum is not None else None,
            )
        )
    )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# The app version. Single source of truth for the API, the update check and the
# banner in the UI. The installer gets its version separately - bump both when
# cutting a release (backend/scripts/build_installer.ps1 -Version x.y.z).
AVE_VERSION = _env_str("AVE_VERSION", "1.0.0")

# Where updates are fetched from (GitHub Releases of the public app repo).
# Overridable so tests never touch the real API.
UPDATE_REPO = _env_str("AVE_UPDATE_REPO", "TonyFlee/ai-video-enhancer")

# ------------------------------------------------------------------ packaging
# The desktop app is a PyInstaller bundle: `sys.frozen` is set and the read-only
# payload (models, UI, FFmpeg) lives under sys._MEIPASS, while everything that
# must stay writable (storage) moves to %LOCALAPPDATA%. In a source checkout the
# same names keep pointing at backend/ and backend/storage as before.
IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) if IS_FROZEN else BASE_DIR
APP_LOCAL_DIR = Path(
    os.environ.get("AVE_LOCAL_DIR")
    or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AI Video Enhancer"
)
BUNDLE_BIN_DIR = RESOURCE_DIR / "bin"   # bundled ffmpeg.exe / ffprobe.exe
BUNDLE_UI_DIR = RESOURCE_DIR / "ui"     # static export of the Next.js frontend

# Static UI: served by FastAPI itself in the desktop app. Off by default in a
# source checkout so `next dev` stays the only UI server during development.
SERVE_UI = _env_bool("AVE_SERVE_UI", IS_FROZEN)
UI_DIR = Path(os.environ["AVE_UI_DIR"]) if os.environ.get("AVE_UI_DIR") else BUNDLE_UI_DIR

MODELS_DIR = Path(
    os.environ["AVE_MODELS_DIR"]
    if os.environ.get("AVE_MODELS_DIR")
    else (RESOURCE_DIR / "models" if IS_FROZEN else BASE_DIR / "models")
)
STORAGE_DIR = Path(
    os.environ["AVE_STORAGE_DIR"]
    if os.environ.get("AVE_STORAGE_DIR")
    else (APP_LOCAL_DIR / "storage" if IS_FROZEN else BASE_DIR / "storage")
)
UPLOADS_DIR = STORAGE_DIR / "uploads"
OUTPUTS_DIR = STORAGE_DIR / "outputs"
TMP_DIR = STORAGE_DIR / "tmp"

RIFE_MODEL_PATH = MODELS_DIR / os.environ.get("AVE_RIFE_MODEL", "RIFE_fp32_timestep.onnx")

# Validation limits
MAX_UPLOAD_BYTES = int(_env_float("AVE_MAX_UPLOAD_GB", 4.0, minimum=0.1, maximum=100.0) * 1024**3)
MAX_DURATION_SECONDS = (
    _env_float("AVE_MAX_DURATION_MIN", 30.0, minimum=0.1, maximum=24 * 60) * 60.0
)
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".ts", ".mts", ".m2ts"}
ALLOWED_VIDEO_CODECS = {
    "h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "wmv2", "wmv3", "prores", "dnxhd", "h263",
}

# Output encoding defaults
DEFAULT_CQ = 19             # NVENC CQ / x264 CRF-ish quality target
X264_PRESET = "slow"
NVENC_PRESET = "p5"
NVENC_TUNE = "hq"

# Job retention: delete job files after this many hours
JOB_TTL_HOURS = _env_float("AVE_JOB_TTL_HOURS", 4.0, minimum=0.1, maximum=24 * 30)

# FFmpeg binary discovery: allow override, then rely on PATH
# FFmpeg binary discovery: explicit override, then PATH, then common Windows install
# locations (a freshly winget-installed FFmpeg is not visible to already-running
# processes, so the PATH lookup alone is not enough).
def _discover_binary(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override

    # A binary shipped inside the app bundle (or next to the .exe) always wins:
    # it is the version the installer was tested against.
    executable = f"{name}.exe" if os.name == "nt" else name
    for bundled in (BUNDLE_BIN_DIR / executable, RESOURCE_DIR / executable):
        if bundled.is_file():
            return str(bundled)

    found = shutil.which(name)
    if found:
        return found

    search_roots: list[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        search_roots.append(Path(local_appdata) / "Microsoft" / "WinGet" / "Packages")
        search_roots.append(Path(local_appdata) / "Microsoft" / "WinGet" / "Links")
    search_roots.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin")
    search_roots.append(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "chocolatey" / "bin")
    search_roots.append(Path("C:/ffmpeg/bin"))
    search_roots.append(Path.home() / "scoop" / "shims")

    for root in search_roots:
        try:
            if not root.exists():
                continue
            direct = root / executable
            if direct.is_file():
                return str(direct)
            for hit in root.glob(f"**/{executable}"):
                return str(hit)
        except OSError:
            continue
    return name  # last resort: rely on PATH at call time


FFMPEG = _discover_binary("ffmpeg", "AVE_FFMPEG")
FFPROBE = _discover_binary("ffprobe", "AVE_FFPROBE")


def ensure_dirs() -> None:
    for d in (UPLOADS_DIR, OUTPUTS_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------- CUDA DLL search path (Windows)
# onnxruntime-gpu loads its CUDA provider DLLs dynamically, but the cuBLAS / cuDNN /
# CUDA runtime DLLs shipped by the `nvidia-*-cu12` wheels live inside site-packages
# and are not on the default search path. Register them so the CUDA execution
# provider can initialise without a system-wide CUDA toolkit installation.
_DLL_HANDLES: list = []


def register_nvidia_dll_dirs() -> list[str]:
    if os.name != "nt":
        return []
    import site

    added: list[str] = []
    roots: list[Path] = []
    # Frozen build: the wheels' DLL trees are copied into the bundle as
    # <bundle>/nvidia/<package>/bin, so look there first (site-packages paths do
    # not exist on the user's machine).
    if IS_FROZEN and (RESOURCE_DIR / "nvidia").is_dir():
        roots.append(RESOURCE_DIR / "nvidia")
    for base in [Path(p) for p in site.getsitepackages()] + [Path(site.getusersitepackages())]:
        nvidia = base / "nvidia"
        if nvidia.is_dir():
            roots.append(nvidia)

    for root in roots:
        for bin_dir in sorted(root.glob("*/bin")):
            if not bin_dir.is_dir():
                continue
            try:
                _DLL_HANDLES.append(os.add_dll_directory(str(bin_dir)))
            except (AttributeError, OSError):
                pass
            current = os.environ.get("PATH", "")
            if str(bin_dir) not in current:
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{current}"
            added.append(str(bin_dir))

    if os.environ.get("AVE_DEBUG_DLL"):
        print(f"[config] nvidia DLL dirs registered: {added}")
    return added


register_nvidia_dll_dirs()


# ---------------------------------------------------------------- GPU memory
def gpu_memory_info() -> dict[str, int] | None:
    """Return {total, used, free} GPU memory in MiB via nvidia-smi, or None.

    Cached for a few seconds because it shells out and is queried per job.
    """
    import time

    global _GPU_MEMO
    now = time.monotonic()
    if _GPU_MEMO and now - _GPU_MEMO[0] < 3.0:
        return _GPU_MEMO[1]

    try:
        proc = subproc.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        total, used, free = (int(x) for x in proc.stdout.strip().splitlines()[0].split(","))
        info = {"total": total, "used": used, "free": free}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None

    _GPU_MEMO = (now, info)
    return info


_GPU_MEMO: tuple[float, dict[str, int]] | None = None


def _binary_works(binary: str) -> bool:
    if os.path.isabs(binary) and not Path(binary).exists():
        return False
    try:
        proc = subproc.run([binary, "-version"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@functools.lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    """Whether both FFmpeg binaries run. Cached: the UI polls /api/health, and
    spawning two processes per poll is pure waste (and used to flash windows)."""
    return _binary_works(FFMPEG) and _binary_works(FFPROBE)


def resolve_ffmpeg() -> str | None:
    return shutil.which(FFMPEG) or (FFMPEG if Path(FFMPEG).exists() else None)
