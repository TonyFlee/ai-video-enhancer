"""Find a reproducible FP16 conversion of the RIFE model that ONNX Runtime accepts.

Tries several converters / block lists, validating each candidate by actually loading it
with ONNX Runtime (a type mismatch surfaces at session creation).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RIFE_MODEL_PATH  # noqa: E402

OUT_DIR = RIFE_MODEL_PATH.parent


def validate(path: Path) -> tuple[bool, str]:
    try:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).replace("\n", " ")[:150]
    try:
        names = [i.name for i in session.get_inputs()]
        h, w = 64, 64
        a = np.random.rand(h, w, 3).astype(np.float32)
        b = np.random.rand(h, w, 3).astype(np.float32)
        feed = {names[0]: np.concatenate([np.transpose(a, (2, 0, 1)), np.transpose(b, (2, 0, 1))], 0)[None]}
        for name in names[1:]:
            feed[name] = np.array(0.5, dtype=np.float32)
        out = session.run(None, feed)[0]
        return True, f"infer ok -> {out.shape} {out.dtype}"
    except Exception as exc:  # noqa: BLE001
        return False, f"load ok but inference failed: {str(exc)[:120]}"


def try_variant(label: str, fn) -> Path | None:
    target = OUT_DIR / f"RIFE_fp16_{label}.onnx"
    try:
        fn(target)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] conversion failed: {str(exc)[:140]}")
        return None
    ok, detail = validate(target)
    size = target.stat().st_size / 1e6
    print(f"[{label}] {'OK  ' if ok else 'BAD '} {size:5.1f} MB - {detail}")
    return target if ok else None


def main() -> int:
    source = RIFE_MODEL_PATH
    print(f"source: {source}")

    def ort_convert(target: Path) -> None:
        from onnxruntime.transformers.float16 import convert_float_to_float16

        model = onnx.load(str(source))
        converted = convert_float_to_float16(model, keep_io_types=True, disable_shape_infer=False)
        onnx.save(converted, str(target))

    def ort_convert_nosif(target: Path) -> None:
        from onnxruntime.transformers.float16 import convert_float_to_float16

        model = onnx.load(str(source))
        converted = convert_float_to_float16(model, keep_io_types=True, disable_shape_infer=True)
        onnx.save(converted, str(target))

    def ort_convert_blocked(target: Path) -> None:
        from onnxruntime.transformers.float16 import convert_float_to_float16

        model = onnx.load(str(source))
        converted = convert_float_to_float16(
            model,
            keep_io_types=True,
            disable_shape_infer=False,
            op_block_list=["Pow", "Div", "Sqrt", "Exp", "Log", "ReduceMean", "Softmax"],
        )
        onnx.save(converted, str(target))

    def ort_convert_nokeep(target: Path) -> None:
        from onnxruntime.transformers.float16 import convert_float_to_float16

        model = onnx.load(str(source))
        converted = convert_float_to_float16(model, keep_io_types=False, disable_shape_infer=False)
        onnx.save(converted, str(target))

    def occ_convert_all(target: Path) -> None:
        from onnxconverter_common import float16

        model = onnx.load(str(source))
        converted = float16.convert_float_to_float16(model, keep_io_types=True, op_block_list=None)
        onnx.save(converted, str(target))

    def occ_convert_blocked(target: Path) -> None:
        from onnxconverter_common import float16

        model = onnx.load(str(source))
        converted = float16.convert_float_to_float16(
            model, keep_io_types=True, op_block_list=["Pow", "Div", "Sqrt"]
        )
        onnx.save(converted, str(target))

    results = {}
    for label, fn in [
        ("ort", ort_convert),
        ("ortnosif", ort_convert_nosif),
        ("ortblocked", ort_convert_blocked),
        ("ortnokeep", ort_convert_nokeep),
        ("occall", occ_convert_all),
        ("occblocked", occ_convert_blocked),
    ]:
        results[label] = try_variant(label, fn)

    good = {k: v for k, v in results.items() if v}
    print("\nusable variants:", list(good.keys()))
    if good:
        final = OUT_DIR / "RIFE_fp16_timestep.onnx"
        best = next(iter(good.values()))
        import shutil

        shutil.copyfile(best, final)
        print(f"copied {best.name} -> {final.name} ({final.stat().st_size / 1e6:.1f} MB)")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
