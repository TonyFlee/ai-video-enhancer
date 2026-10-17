"""Download the pretrained RIFE ONNX model used for frame interpolation.

Usage (from the backend folder, with the venv active):

    python scripts/download_models.py
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

MODELS = {
    # Exposes RIFE's timestep as a runtime input -> arbitrary interpolation phases
    # (better quality for fractional factors such as 24 -> 60 fps than bisection).
    # Packed input [1, 6, H, W]: frame0 in channels 0-2, frame1 in 3-5.
    "RIFE_fp32_timestep.onnx": {
        "url": "https://huggingface.co/walterlow/RIFE_fp32_timestep/resolve/main/RIFE_fp32_timestep.onnx",
        "sha256": None,
        "size": 21_604_631,
        "required": True,
    },
    # Upstream midpoint-only export (t = 0.5). Optional: used automatically if the
    # timestep model is missing; fractional phases then come from bisection.
    "RIFE_fp32.onnx": {
        "url": "https://huggingface.co/FuryTMP/RIFE_fp32/resolve/main/RIFE_fp32.onnx",
        "sha256": None,
        "size": 21_604_631,
        "required": False,
    },
}


def download(name: str, url: str, expected_size: int | None = None, sha256: str | None = None) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = MODELS_DIR / name
    if target.exists() and (expected_size is None or target.stat().st_size == expected_size):
        print(f"[skip] {name} already present ({target.stat().st_size} bytes)")
        return target

    print(f"[get ] {name} <- {url}")
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        read = 0
        while chunk := response.read(1024 * 256):
            handle.write(chunk)
            read += len(chunk)
            if total:
                pct = read / total * 100
                sys.stdout.write(f"\r       {pct:5.1f}%  {read / 1e6:6.1f} MB")
                sys.stdout.flush()
    print()

    if sha256:
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest != sha256:
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"Checksum mismatch for {name}: {digest} != {sha256}")

    if expected_size and tmp.stat().st_size != expected_size:
        print(f"[warn] {name} size {tmp.stat().st_size} != expected {expected_size} (continuing)")

    tmp.replace(target)
    print(f"[done] {name} -> {target}")
    return target


def main() -> None:
    failures: list[str] = []
    for name, meta in MODELS.items():
        try:
            download(name, meta["url"], meta.get("size"), meta.get("sha256"))
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            if meta.get("required"):
                print(f"[fail] {name}: {exc}")
                raise SystemExit(1) from exc
            print(f"[warn] optional model {name} skipped: {exc}")
    print("\nAll required models ready." if not failures else f"\nDone (skipped: {', '.join(failures)})")


if __name__ == "__main__":
    main()
