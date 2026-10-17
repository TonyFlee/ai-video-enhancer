# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller bundle for the packaged Windows desktop app.

Build it with backend/scripts/build_windows_app.ps1 (which also exports the UI and
copies FFmpeg), or directly:

    .\\.venv\\Scripts\\pyinstaller packaging\\app.spec --noconfirm

What ends up in the bundle:
  ui/      statically exported Next.js frontend (served by FastAPI)
  models/  RIFE ONNX models
  bin/     ffmpeg.exe + ffprobe.exe copied from the build machine
  nvidia/  the CUDA runtime / cuDNN / cuBLAS DLLs from the nvidia-*-cu12 wheels,
           kept in their original <package>/bin layout so app/config.py's
           register_nvidia_dll_dirs() finds them exactly like in the venv
  _internal/onnxruntime, uvicorn, webview, fastapi, ...

Set AVE_CPU_ONLY=1 to drop the nvidia DLLs and build a much smaller CPU-only app.
"""
import os
import sys
from pathlib import Path

from PyInstaller.building.api import PYZ, EXE, COLLECT
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.osx import BUNDLE  # noqa: F401  (kept for parity; unused on Windows)
from PyInstaller.utils.hooks import collect_all, collect_submodules

SPECDIR = Path(SPECPATH).resolve()
BACKEND = SPECDIR.parent
FRONTEND_OUT = BACKEND.parent / "frontend" / "out"
MODELS_DIR = BACKEND / "models"
BIN_DIR = SPECDIR / "bin"
# PyInstaller executes the entry script as a top-level module, so it must import
# the `app` package rather than live inside it (relative imports would break).
ENTRY = SPECDIR / "entry.py"

APP_NAME = "AI Video Enhancer"
CPU_ONLY = os.environ.get("AVE_CPU_ONLY", "") in ("1", "true", "yes")

# ------------------------------------------------------------------ bundled UI
datas: list[tuple] = []
if FRONTEND_OUT.is_dir() and (FRONTEND_OUT / "index.html").is_file():
    datas.append((str(FRONTEND_OUT), "ui"))
else:
    print(f"[spec] WARNING: no static UI at {FRONTEND_OUT} — run the frontend export first")

# ------------------------------------------------------------------- models
if MODELS_DIR.is_dir():
    datas += [
        (str(path), "models")
        for path in sorted(MODELS_DIR.glob("*.onnx"))
        if path.stat().st_size < 100 * 1024 * 1024
    ]

# ------------------------------------------------------------- ffmpeg binaries
binaries: list[tuple] = []
for name in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = BIN_DIR / name
    if candidate.is_file():
        binaries.append((str(candidate), "bin"))
    else:
        print(f"[spec] WARNING: {name} not in {BIN_DIR}; the app will look for FFmpeg on PATH")

# --------------------------------------------------------------- onnxruntime + CUDA
ort_datas, ort_binaries, ort_hidden = collect_all("onnxruntime")
datas += ort_datas
binaries += ort_binaries


def _nvidia_dlls() -> list[tuple]:
    """Copy the nvidia-*-cu12 wheels' DLL trees into the bundle, layout intact.

    The wheels ship far more than ONNX Runtime's CUDA EP ever maps. Two
    independent checks agree on the keep-list:

      * ORT's own required-DLL list (onnxruntime/__init__.py::
        _get_nvidia_dll_paths) - cuBLAS/cuFFT/cuDNN only;
      * scripts/scan_nvidia_usage.py - loads the shipped model through
        app.rife.RifeEngine.warmup() and lists what actually gets mapped
        (11 of 23 DLLs; cuSPARSE/cuSOLVER never load).

    Everything below is trimmed from the payload: ~1 GB, and the difference
    between an installer over and under GitHub's 2 GB release-asset cap.
    """
    trim_packages = {"cusparse", "cusolver"}  # 904 MB, referenced by nothing that runs
    trim_files = {
        "nvidia/cublas/bin/nvblas64_12.dll",  # BLAS-compatibility shim
        "nvidia/cufft/bin/cufftw64_11.dll",  # Fortran wrappers
        "nvidia/cuda_nvrtc/bin/nvrtc64_120_0.alt.dll",  # orphaned alternate compiler
    }
    candidates = [Path(sys.prefix) / "Lib" / "site-packages"]
    import site

    candidates += [Path(p) for p in site.getsitepackages()]
    collected: dict[str, tuple] = {}
    seen_roots: set[Path] = set()
    trimmed_count = 0
    trimmed_bytes = 0
    for site_packages in candidates:
        # sys.prefix/Lib/site-packages and site.getsitepackages() can name the
        # same folder twice - count every trimmed file only once.
        site_packages = site_packages.resolve()
        if site_packages in seen_roots:
            continue
        seen_roots.add(site_packages)
        nvidia = site_packages / "nvidia"
        if not nvidia.is_dir():
            continue
        for path in nvidia.rglob("*"):
            if path.suffix.lower() not in {".dll", ".pyd", ".so"}:
                continue
            rel = path.relative_to(site_packages)
            posix = rel.as_posix()
            unused = (
                len(rel.parts) >= 2
                and rel.parts[0] == "nvidia"
                and rel.parts[1] in trim_packages
            ) or posix in trim_files
            if unused:
                trimmed_count += 1
                trimmed_bytes += path.stat().st_size
                continue
            collected.setdefault(posix, (str(path), str(rel.parent)))
    if trimmed_count:
        print(
            f"[spec] trimmed {trimmed_count} unused nvidia DLLs "
            f"({trimmed_bytes / 1048576:.0f} MB): cuSPARSE, cuSOLVER, nvBLAS, ..."
        )
    return list(collected.values())


hiddenimports = sorted(set(ort_hidden))
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["webview", "clr", "clr_loader", "pythonnet"]

if not CPU_ONLY:
    nvidia = _nvidia_dlls()
    if nvidia:
        binaries += nvidia
        print(f"[spec] bundling {len(nvidia)} CUDA/cuDNN DLLs from the nvidia wheels")
    else:
        print("[spec] WARNING: AVE_CPU_ONLY not set but no nvidia DLLs were found")
else:
    print("[spec] CPU-only build: skipping the nvidia CUDA DLLs")

# Everything the AI conversion tooling needs is build-time only.
excludes = [
    "onnx",
    "onnxconverter_common",
    "pytest",
    "requests",
    "tkinter",
    "test",
    "IPython",
]

analysis = Analysis(
    [str(ENTRY)],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                     # UPX breaks CUDA/cuDNN DLLs
    console=False,                 # windowed app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPECDIR / "app.ico") if (SPECDIR / "app.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
