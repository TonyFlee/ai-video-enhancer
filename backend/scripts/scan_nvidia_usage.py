"""Which NVIDIA DLLs does a *real* RIFE CUDA run actually load?

The nvidia-*-cu12 wheels ship ~3.3 GB of libraries; ONNX Runtime's CUDA EP maps
only a fraction of them. packaging/app.spec trims the rest (that ~1 GB is what
keeps the installer under GitHub's 2 GB release-asset cap), and this script is
the evidence behind that decision:

  1. loads the model through app.rife.RifeEngine - the exact code path a job
     takes - and runs warmup() (one real CUDA inference on your GPU);
  2. lists every DLL mapped into the process;
  3. marks each DLL in the bundle (or site-packages) as loaded / not loaded.

Two independent keep-lists must agree before a DLL is trimmed:
  * onnxruntime/__init__.py::_get_nvidia_dll_paths (ORT's own required list)
  * this script's LOADED column after a real run

Usage:
    .venv\\Scripts\\python.exe scripts\\scan_nvidia_usage.py
    .venv\\Scripts\\python.exe scripts\\scan_nvidia_usage.py --bundle "dist\\AI Video Enhancer"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Importing app.config registers the wheels' DLL directories, exactly like
# production (app/rife.py does the same before building a session).
from app.config import MODELS_DIR, register_nvidia_dll_dirs  # noqa: E402

register_nvidia_dll_dirs()

import psutil  # noqa: E402

from app.rife import RifeEngine  # noqa: E402

# What packaging/app.spec trims. Keep this list in sync with _nvidia_dlls().
TRIM_PACKAGES = {"cusparse", "cusolver"}
TRIM_FILES = {
    "nvblas64_12.dll",
    "cufftw64_11.dll",
    "nvrtc64_120_0.alt.dll",
}

# ORT's own required-DLL list (Windows) - anything here must stay regardless.
ORT_REQUIRED = {
    "cublas64_12.dll",
    "cublaslt64_12.dll",
    "cufft64_11.dll",
    "cudart64_12.dll",
    "cudnn64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_heuristic64_9.dll",
    "cudnn_ops64_9.dll",
}


def nvidia_dirs(bundle: Path | None) -> list[Path]:
    """The DLL trees to compare against: a built bundle, else site-packages."""
    if bundle is not None:
        frozen = bundle / "_internal" / "nvidia"
        target = frozen if frozen.is_dir() else bundle / "nvidia"
        if target.is_dir():
            return [target]
        raise SystemExit(f"no nvidia/ folder under {bundle}")

    import site

    roots = []
    for sp in [Path(sys.prefix) / "Lib" / "site-packages", *map(Path, site.getsitepackages())]:
        if (sp / "nvidia").is_dir():
            roots.append(sp / "nvidia")
    if not roots:
        raise SystemExit("no nvidia wheels found in site-packages")
    return roots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=BACKEND / "dist" / "AI Video Enhancer",
        help="built app folder to audit (default: dist\\AI Video Enhancer)",
    )
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    args = parser.parse_args()

    bundle = args.bundle if args.bundle.is_dir() else None
    roots = nvidia_dirs(bundle)
    where = "bundle" if bundle else "site-packages"

    shipped: dict[str, Path] = {}
    for root in roots:
        for path in root.rglob("*.dll"):
            shipped.setdefault(path.name.lower(), path)

    model = MODELS_DIR / "RIFE_fp32_timestep.onnx"
    if not model.is_file():
        raise SystemExit(f"model not found: {model}")

    print(f"auditing {len(shipped)} nvidia DLLs from {where}: {roots[0]}")
    engine = RifeEngine(model, prefer_gpu=True)
    print(
        f"session: gpu={engine.using_gpu} providers={engine._providers} "
        f"mode={engine._mode} timestep={engine._timestep_input}"
    )
    if not engine.using_gpu:
        print("WARNING: no CUDA in this run - the loaded column proves nothing about the GPU path")

    engine.warmup(args.width, args.height)
    print(f"warmup: one real inference at {args.width}x{args.height} OK\n")

    loaded = {os.path.basename(m.path).lower() for m in psutil.Process().memory_maps() if m.path}

    print(f"{'verdict':12} {'MB':>7}  dll (trimmed by app.spec / required by ORT)")
    print("-" * 78)
    trimmable, loaded_n, not_loaded_n = 0, 0, 0
    for name in sorted(shipped):
        size = shipped[name].stat().st_size / 1048576
        is_loaded = name in loaded
        if is_loaded:
            loaded_n += 1
            verdict = "LOADED"
        else:
            not_loaded_n += 1
            verdict = "not loaded"
        tags = []
        if name in TRIM_FILES or any(f"/{p}/" in f"/{shipped[name].parts[-3]}/" for p in []):
            tags.append("TRIMMED")
        if name in TRIM_PACKAGES:
            tags.append("TRIMMED")
        if name in ORT_REQUIRED:
            tags.append("ORT-required")
        suffix = f"   [{', '.join(tags)}]" if tags else ""
        if not is_loaded and name not in ORT_REQUIRED:
            trimmable += size
        print(f"{verdict:12} {size:7.0f}  {name}{suffix}")

    print("-" * 78)
    print(f"loaded {loaded_n} / {len(shipped)}; not loaded {not_loaded_n}")
    print(f"not loaded and not ORT-required: {trimmable:.0f} MB")
    print(
        "\nBefore trimming anything: every DLL you remove must stay absent here "
        "AND stay off onnxruntime's required list."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
