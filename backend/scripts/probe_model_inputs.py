"""Figure out what the single-input RIFE ONNX export expects.

Feeds a few candidate shapes and reports which ones succeed and what comes back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RIFE_MODEL_PATH  # noqa: E402


def describe(name: str, value) -> str:
    if isinstance(value, np.ndarray):
        return f"ndarray shape={value.shape} dtype={value.dtype} mean={value.mean():.4f}"
    return str(value)


def main() -> None:
    session = ort.InferenceSession(str(RIFE_MODEL_PATH), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    h, w = 64, 64
    rng = np.random.default_rng(0)
    frame_a = rng.random((1, 3, h, w), dtype=np.float32)
    frame_b = rng.random((1, 3, h, w), dtype=np.float32)

    candidates = {
        "batch=2 stacked pair": np.concatenate([frame_a, frame_b], axis=0),
        "single frame (1,3,h,w)": frame_a,
        "batch=4": np.concatenate([frame_a, frame_b, frame_a, frame_b], axis=0),
        "batch=1 channel-stacked (1,6,h,w)": np.concatenate([frame_a[0], frame_b[0]], axis=0)[None],
    }

    for label, value in candidates.items():
        try:
            outputs = session.run([output_name], {input_name: value})
            print(f"[ok  ] {label}: {[describe(o, o) for o in outputs]}")
        except Exception as exc:  # noqa: BLE001
            message = str(exc).replace("\n", " ")[:160]
            print(f"[fail] {label}: {message}")

    # Dump the graph so the slicing/packing is visible.
    try:
        import onnx

        model = onnx.load(str(RIFE_MODEL_PATH))
        print("\ngraph nodes:", len(model.graph.node))
        print("inputs:", [(i.name, [d.dim_value or d.dim_param for d in i.type.tensor_type.shape.dim]) for i in model.graph.input])
        print("outputs:", [(o.name, [d.dim_value or d.dim_param for d in o.type.tensor_type.shape.dim]) for o in model.graph.output])
        print("first 12 ops:", [(n.op_type, list(n.input)[:3]) for n in model.graph.node[:12]])
        print("last 8 ops:", [(n.op_type, list(n.input)[:3]) for n in model.graph.node[-8:]])
    except Exception as exc:  # noqa: BLE001
        print("onnx graph dump unavailable:", exc)


if __name__ == "__main__":
    main()
