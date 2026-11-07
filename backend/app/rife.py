"""RIFE frame interpolation via ONNX Runtime (CUDA when available, CPU fallback).

Supported ONNX export shapes
----------------------------
1. **Packed channels** (default, `RIFE_fp32.onnx` / `RIFE_fp32_timestep.onnx`):
   a single rank-4 input `[1, 6, H, W]` where frame0 occupies channels 0-2 and frame1
   channels 3-5, planar RGB in [0, 1]. The graph pads to a multiple of 32 internally and
   crops back, so the caller must not pad.
2. **Packed batch**: a single rank-4 input with batch dim 2 and 3 channels `[2, 3, H, W]`.
3. **Two inputs**: `img0` / `img1` (or `I0` / `I1`) rank-4 tensors, as exported by
   ComfyUI-style RIFE conversions.

Any scalar (rank-0/1) input is treated as the interpolation timestep. Models without one
only produce the midpoint frame (t = 0.5); fractional phases are then produced by the
caller via memoised bisection (`PairInterpolator` in pipeline.py).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from .config import gpu_memory_info, register_nvidia_dll_dirs

logger = logging.getLogger(__name__)

TILE_OVERLAP = 32          # pixels of context when tiling
MIN_TILE_HEIGHT = 96       # never shrink below this; below it the overlap dominates
# Tile height used when memory is tight. Tuned on a 4 GB card: 384 gave the best
# throughput without the cuDNN workspace thrashing seen at 640+. Override with
# AVE_TILE_HEIGHT for other GPUs.
try:
    DEFAULT_TILE_HEIGHT = max(MIN_TILE_HEIGHT, int(os.environ.get("AVE_TILE_HEIGHT", "384")))
except (TypeError, ValueError):
    DEFAULT_TILE_HEIGHT = 384

# Rough peak working-set per output pixel for a full-frame fp32 RIFE pass on CUDA,
# derived from measurements (720p ~= 0.92 MP spiked past the ~950 MB that was free on
# the 4 GB test card, while tiled inference stayed fast). Includes cuDNN scratch.
BYTES_PER_PIXEL = 1400
# Leave this much VRAM for the desktop / cuDNN scratch allocations.
VRAM_HEADROOM_MB = 320


class RifeError(Exception):
    pass


def _is_oom(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(k in text for k in (
        "out of memory", "cuda_error_out_of_memory", "failed to allocate",
        "bfc_arena", "memory allocation", "cuda failure", "insufficient memory",
    ))


class RifeEngine:
    """Thread-safe wrapper around a RIFE ONNX session."""

    def __init__(self, model_path: Path, prefer_gpu: bool = True) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise RifeError(
                f"RIFE model not found at {self.model_path}. "
                "Run `python scripts/download_models.py` or see the README."
            )
        self._lock = threading.Lock()
        self._session = None
        self._providers: list[str] = []
        self._available_providers: list[str] = []
        self._use_tiles = False
        self._tile_height = DEFAULT_TILE_HEIGHT
        self._tile_overlap = TILE_OVERLAP
        self._vram_limit_mb: int | None = None
        self.using_gpu = False
        self.last_error: str | None = None

        # Graph plan
        self._mode: str = "packed-channels"   # packed-channels | packed-batch | two-input
        self._frame_inputs: tuple[str, str] = ("", "")
        self._packed_input: str = ""
        self._timestep_input: str | None = None
        self._timestep_shape_rank: int = 0
        self._output_name: str = ""

        self._load(prefer_gpu=prefer_gpu)

    # ---------------------------------------------------------------- loading
    def _session_options(self):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 0
        opts.log_severity_level = 3
        return opts

    def _build_session(self, use_gpu: bool):
        import onnxruntime as ort

        self._vram_limit_mb: int | None = None
        if use_gpu:
            # Make sure the CUDA/cuBLAS/cuDNN DLLs from the pip wheels are findable.
            register_nvidia_dll_dirs()

        available = ort.get_available_providers()
        providers: list = []
        if use_gpu and "CUDAExecutionProvider" in available:
            provider_options: dict = {
                "cudnn_conv_algo_search": "HEURISTIC",
                # Without this the cuDNN heuristic can pick an algorithm whose workspace
                # allocation fails on a 4 GB card and makes convolution 20-50x slower.
                "cudnn_conv_use_max_workspace": "1",
                "arena_extend_strategy": "kSameAsRequested",
                "do_copy_in_default_stream": True,
            }
            # Windows' WDDM silently spills GPU allocations into shared system memory
            # instead of failing, which turns an out-of-memory situation into a 50x
            # slowdown rather than the clean error our tiling fallback expects. Cap the
            # arena so a genuine over-allocation raises and tiling can take over.
            info = gpu_memory_info()
            if info and info.get("free", 0) > 0:
                limit_mb = int(info["free"] * 0.85)
                if limit_mb >= 384:
                    provider_options["gpu_mem_limit"] = str(limit_mb * 1024 * 1024)
                    self._vram_limit_mb = limit_mb
            providers.append(("CUDAExecutionProvider", provider_options))
        providers.append("CPUExecutionProvider")
        session = ort.InferenceSession(
            str(self.model_path), sess_options=self._session_options(), providers=providers
        )
        return session, list(available)

    def _load(self, prefer_gpu: bool) -> None:
        session = available = None
        if prefer_gpu:
            try:
                session, available = self._build_session(use_gpu=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("CUDA session creation failed (%s); trying CPU", exc)

        if session is None:
            try:
                session, available = self._build_session(use_gpu=False)
            except Exception as exc:  # noqa: BLE001
                raise RifeError(f"Could not load RIFE ONNX model: {exc}") from exc

        self._session = session
        self._providers = list(session.get_providers())
        self._available_providers = available or []
        self.using_gpu = bool(self._providers) and self._providers[0] == "CUDAExecutionProvider"
        self._introspect()
        logger.info(
            "RIFE loaded | model=%s | providers=%s | mode=%s | timestep=%s",
            self.model_path.name, self._providers, self._mode, self._timestep_input or "baked",
        )

    def _introspect(self) -> None:
        inputs = self._session.get_inputs()
        if not inputs:
            raise RifeError("RIFE model has no inputs.")

        image_inputs = []
        for inp in inputs:
            shape = list(inp.shape)
            if len(shape) == 4 or (len(shape) == 5):
                image_inputs.append(inp)
            elif len(shape) <= 1:
                self._timestep_input = inp.name
                self._timestep_shape_rank = len(shape)

        if not image_inputs:
            raise RifeError("RIFE model has no rank-4 image input.")

        if len(image_inputs) >= 2:
            self._mode = "two-input"
            names = [i.name for i in image_inputs[:2]] + [i.name for i in inputs if i not in image_inputs]

            def sort_key(name: str) -> int:
                lowered = name.lower()
                if lowered.endswith("0") or "img0" in lowered or "frame0" in lowered:
                    return 0
                if lowered.endswith("1") or "img1" in lowered or "frame1" in lowered:
                    return 1
                return 2

            ordered = sorted([i.name for i in image_inputs[:2]], key=sort_key)
            self._frame_inputs = (ordered[0], ordered[1])
        else:
            packed = image_inputs[0]
            self._packed_input = packed.name
            shape = list(packed.shape)
            # Static dims only; "dynamic_dim_N" placeholders are strings.
            static = [d if isinstance(d, int) else None for d in shape]
            batch, channels = static[0], static[1]
            if channels == 3 and batch == 2:
                self._mode = "packed-batch"
            else:
                self._mode = "packed-channels"

        outputs = self._session.get_outputs()
        if not outputs:
            raise RifeError("RIFE model has no outputs.")
        self._output_name = outputs[0].name

    # ------------------------------------------------------------- properties
    @property
    def device(self) -> str:
        return "CUDA (GPU)" if self.using_gpu else "CPU"

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    @property
    def available_providers(self) -> list[str]:
        return list(self._available_providers)

    @property
    def supports_timestep(self) -> bool:
        return self._timestep_input is not None

    @property
    def tiling(self) -> bool:
        return self._use_tiles

    def begin_job(self) -> None:
        """Reset per-job tiling state.

        A previous job may have switched to tiling (or shrunk the tile height) after
        an OOM. That decision was specific to that job's frame size and free VRAM,
        so keeping it forever made every later job slower. The next frame calls
        ``_auto_tile_for`` again and re-enables tiling if it is still needed.
        """
        with self._lock:
            self.last_error = None
            if self.using_gpu:
                self._use_tiles = False
                self._tile_height = DEFAULT_TILE_HEIGHT

    def describe(self) -> dict:
        return {
            "loaded": True,
            "device": self.device,
            "providers": self.providers,
            "availableProviders": self.available_providers,
            "model": self.model_path.name,
            "modelSizeBytes": self.model_path.stat().st_size,
            "mode": self._mode,
            "tiling": self._use_tiles,
            "tileHeight": self._tile_height if self._use_tiles else None,
            "vramLimitMb": self._vram_limit_mb,
            "supportsTimestep": self.supports_timestep,
            "timestepInput": self._timestep_input,
            "lastError": self.last_error,
        }

    # -------------------------------------------------------------- inference
    @staticmethod
    def _to_planar(frame_rgb: np.ndarray) -> np.ndarray:
        """uint8 HWC RGB -> float32 CHW in [0, 1]."""
        return np.ascontiguousarray(
            np.transpose(frame_rgb.astype(np.float32) / 255.0, (2, 0, 1))
        )

    def _pack_planar(self, pa: np.ndarray, pb: np.ndarray, t: float | None) -> dict:
        """Build the ONNX feed from already-converted planar CHW float32 frames."""
        feed: dict[str, np.ndarray] = {}

        if self._mode == "two-input":
            feed[self._frame_inputs[0]] = pa[None, ...]
            feed[self._frame_inputs[1]] = pb[None, ...]
        elif self._mode == "packed-batch":
            feed[self._packed_input] = np.stack([pa, pb], axis=0)
        else:  # packed-channels
            feed[self._packed_input] = np.concatenate([pa, pb], axis=0)[None, ...]

        if self._timestep_input is not None:
            value = np.array(t if t is not None else 0.5, dtype=np.float32)
            if self._timestep_shape_rank == 1:
                value = value.reshape(1)
            feed[self._timestep_input] = value
        return feed

    def _infer_raw(self, a: np.ndarray, b: np.ndarray, t: float | None) -> np.ndarray:
        h, w = a.shape[:2]
        return self._infer_planar(self._to_planar(a), self._to_planar(b), t, h, w)

    def _infer_planar(
        self, pa: np.ndarray, pb: np.ndarray, t: float | None, h: int, w: int
    ) -> np.ndarray:
        """Run one inference on planar CHW frames and return a uint8 RGB frame."""
        out = np.asarray(self._session.run([self._output_name], self._pack_planar(pa, pb, t))[0])
        if out.ndim == 4:
            out = out[0]
        if out.shape[0] == 3:
            out = np.transpose(out, (1, 2, 0))
        out = np.clip(out, 0.0, 1.0) * 255.0
        out = out[:h, :w]
        return np.ascontiguousarray(out.astype(np.uint8))

    def _infer_tiled(self, a: np.ndarray, b: np.ndarray, t: float | None) -> np.ndarray:
        h, w = a.shape[:2]
        result = np.empty_like(a)
        # Convert each frame to planar ONCE and slice tiles from it. Converting inside
        # the loop re-did the float+transpose for every overlapping tile (~2.5x the
        # frame's pixels per interpolation), which was pure CPU waste.
        pa = self._to_planar(a)
        pb = self._to_planar(b)
        overlap = self._tile_overlap
        step = max(self._tile_height - overlap * 2, overlap * 2)
        for y0 in range(0, h, step):
            y1 = min(y0 + self._tile_height, h)
            read_y0 = max(0, y0 - overlap)
            read_y1 = min(h, y1 + overlap)
            tile = self._infer_planar(
                pa[:, read_y0:read_y1], pb[:, read_y0:read_y1], t, read_y1 - read_y0, w
            )
            top_trim = y0 - read_y0
            bottom_trim = read_y1 - y1
            result[y0:y1] = tile[top_trim: tile.shape[0] - bottom_trim]
        return result

    def _auto_tile_for(self, width: int, height: int) -> None:
        """Enable tiling up-front when a full-frame pass clearly will not fit in VRAM.

        WDDM does not raise a clean OOM when the card is oversubscribed, so we cannot
        rely on the exception path alone; estimate the working set instead.
        """
        if self._use_tiles or not self.using_gpu:
            return
        info = gpu_memory_info()
        if not info:
            return
        pixels = width * height
        needed_mb = pixels * BYTES_PER_PIXEL / (1024 * 1024)
        budget_mb = info.get("free", 0)
        if self._vram_limit_mb:
            budget_mb = min(budget_mb, self._vram_limit_mb)
        if needed_mb + VRAM_HEADROOM_MB > budget_mb:
            logger.info(
                "Frame %dx%d needs ~%.0f MB but only ~%d MB VRAM is usable; using tiles.",
                width, height, needed_mb, budget_mb,
            )
            self._use_tiles = True

    def interpolate(self, a: np.ndarray, b: np.ndarray, t: float | None = None) -> np.ndarray:
        """Return the frame between `a` and `b` at phase `t` (0..1); midpoint when unsupported."""
        if a.shape != b.shape:
            raise RifeError("Interpolation frames must have identical dimensions.")
        if t is not None and not self.supports_timestep and abs(t - 0.5) > 1e-6:
            raise RifeError("This RIFE model only supports midpoint interpolation.")
        if t is not None:
            t = float(min(max(t, 1e-4), 1 - 1e-4))

        with self._lock:
            self._auto_tile_for(a.shape[1], a.shape[0])
            try:
                if self._use_tiles:
                    return self._infer_tiled(a, b, t)
                return self._infer_raw(a, b, t)
            except Exception as exc:  # noqa: BLE001
                if not _is_oom(exc):
                    self.last_error = str(exc)
                    raise RifeError(f"Frame interpolation failed: {exc}") from exc

                self.last_error = str(exc)
                if not self._use_tiles:
                    logger.warning("GPU memory low, enabling tiled inference: %s", exc)
                    self._use_tiles = True

                # Tile inference can still run out of VRAM on a small card. Shrink
                # the tile once (and again if needed) before giving up on the GPU;
                # falling back to CPU is correct but roughly two orders of magnitude
                # slower, so it is the last resort.
                for _ in range(4):
                    try:
                        return self._infer_tiled(a, b, t)
                    except Exception as tile_exc:  # noqa: BLE001
                        if not _is_oom(tile_exc) or self._tile_height <= MIN_TILE_HEIGHT:
                            logger.warning("Tiled inference failed (%s); falling back to CPU", tile_exc)
                            break
                        self._tile_height = max(MIN_TILE_HEIGHT, self._tile_height // 2)
                        logger.warning(
                            "Tiled inference ran out of memory; retrying with tile height %d",
                            self._tile_height,
                        )

                self._fallback_to_cpu()
                return self._infer_raw(a, b, t)

    def _fallback_to_cpu(self) -> None:
        if not self.using_gpu:
            return
        logger.warning("Falling back to the CPU execution provider for RIFE.")
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(self.model_path),
                sess_options=self._session_options(),
                providers=["CPUExecutionProvider"],
            )
            self.using_gpu = False
            self._providers = list(self._session.get_providers())
            self._use_tiles = False
        except Exception as exc:  # noqa: BLE001
            raise RifeError(f"CPU fallback failed: {exc}") from exc

    def warmup(self, width: int = 256, height: int = 256) -> None:
        """Run one dummy inference so provider/OOM problems surface before a job starts."""
        blank = np.zeros((height, width, 3), dtype=np.uint8)
        self.interpolate(blank, blank, 0.5 if self.supports_timestep else None)
