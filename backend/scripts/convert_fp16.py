"""Convert the FP32 RIFE ONNX model to mixed-precision FP16 for GPU inference.

FP16 halves the activation memory and is markedly faster on the GPU, which matters on
cards with 4 GB of VRAM (the FP32 graph sits right at the limit at 1080 x 1920 and can
start paging to system memory).

IO stays FP32 (`keep_io_types=True`) so callers feed/read float32 exactly as before, and
numerically sensitive ops stay in FP32.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe scripts\\convert_fp16.py
    .\\.venv\\Scripts\\python.exe scripts\\convert_fp16.py --input models/RIFE_fp32.onnx --output models/RIFE_fp16.onnx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# Ops that must remain FP32: they are numerically sensitive or unsupported in FP16.
OP_BLOCK_LIST = ["Pow", "Div", "Sqrt", "Exp", "Log", "ReduceMean", "Softmax"]


def convert(source: Path, target: Path) -> None:
    import onnx
    from onnxconverter_common import float16

    if target.exists():
        print(f"[skip] {target.name} already exists")
        return
    if not source.exists():
        raise SystemExit(f"source model not found: {source}")

    print(f"[load] {source} ({source.stat().st_size / 1e6:.1f} MB)")
    model = onnx.load(str(source))
    print(f"[conv] opset={model.opset_import[0].version}, nodes={len(model.graph.node)}")
    converted = float16.convert_float_to_float16(
        model,
        keep_io_types=True,
        op_block_list=OP_BLOCK_LIST,
    )
    onnx.checker.check_model(converted)
    onnx.save(converted, str(target))
    print(f"[done] {target} ({target.stat().st_size / 1e6:.1f} MB)")
    print("       inputs :", [(i.name, i.type.tensor_type.elem_type) for i in converted.graph.input])
    print("       outputs:", [(o.name, o.type.tensor_type.elem_type) for o in converted.graph.output])
    print("       (1 = float32, 10 = float16)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(MODELS_DIR / "RIFE_fp32_timestep.onnx"))
    parser.add_argument("--output", default=str(MODELS_DIR / "RIFE_fp16_timestep.onnx"))
    args = parser.parse_args()
    convert(Path(args.input), Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
