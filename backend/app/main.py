"""FastAPI application: upload, process, monitor, preview and download endpoints."""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import subproc

from .config import (
    ALLOWED_VIDEO_EXTENSIONS,
    MAX_DURATION_SECONDS,
    MAX_UPLOAD_BYTES,
    UPLOADS_DIR,
    JOB_TTL_HOURS,
    SERVE_UI,
    UI_DIR,
    ensure_dirs,
    ffmpeg_available,
    gpu_memory_info,
    resolve_ffmpeg,
)
from .ffmpeg import choose_encoder, detect_capabilities
from .jobs import JobManager, new_job_id
from .probe import ProbeError, probe_video
from .rife import RifeError
from .schemas import JobSettings, JobState, PRESET_DIMENSIONS, PRESET_BITRATE_HINT, QUALITY_CQ, SystemInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

manager = JobManager()
CHUNK_SIZE = 1024 * 1024 * 4
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    manager.purge_all_files()  # never keep files from a previous run
    manager.warmup_async()
    stop = threading.Event()

    def _cleanup_loop() -> None:
        while not stop.wait(900):
            try:
                manager.cleanup_expired()
            except Exception:  # noqa: BLE001
                logger.exception("Cleanup loop error")

    thread = threading.Thread(target=_cleanup_loop, name="cleanup", daemon=True)
    thread.start()
    logger.info("AI Video Enhancer backend ready (storage=%s)", UPLOADS_DIR.parent)
    yield
    stop.set()


app = FastAPI(title="AI Video Enhancer API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


# --------------------------------------------------------------------- helpers
def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name or "video")
    name = SAFE_NAME.sub("_", name)
    return name[:120] or "video"


def _get_job_or_404(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found (it may have expired).")
    return job


def _range_response(path: Path, request: Request, *, as_attachment: bool, download_name: str | None) -> Response:
    """Serve a file with HTTP range support so <video> can seek."""
    file_size = path.stat().st_size
    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"
    range_header = request.headers.get("range")
    disposition = (
        f'attachment; filename="{download_name}"' if as_attachment else "inline"
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": disposition,
        "Cache-Control": "no-store",
    }

    if not range_header:
        headers["Content-Length"] = str(file_size)

        def full() -> Iterator[bytes]:
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    yield chunk

        return StreamingResponse(full(), media_type=media_type, headers=headers)

    match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Invalid Range header")
    start_raw, end_raw = match.groups()
    if start_raw:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1
    else:  # suffix range: bytes=-N
        length = int(end_raw or 0)
        start = max(file_size - length, 0)
        end = file_size - 1
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    length = end - start + 1
    headers["Content-Length"] = str(length)
    headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    def partial() -> Iterator[bytes]:
        remaining = length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(partial(), status_code=206, media_type=media_type, headers=headers)


@functools.lru_cache(maxsize=1)
def _gpu_name() -> str | None:
    try:
        proc = subproc.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None


# ---------------------------------------------------------------------- routes
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ffmpeg": ffmpeg_available()}


@app.get("/health", include_in_schema=False)
def health_alias() -> dict:
    """Convenience alias for external probes that expect a root-level health check."""
    return health()


@app.get("/api/system", response_model=SystemInfo)
def system_info() -> SystemInfo:
    caps = detect_capabilities()
    engine, engine_error = manager.engine_status()
    h264 = h265 = None
    try:
        h264 = choose_encoder("h264").name
    except Exception:  # noqa: BLE001
        h264 = None
    try:
        h265 = choose_encoder("h265").name
    except Exception:  # noqa: BLE001
        h265 = None

    free_disk = shutil.disk_usage(str(UPLOADS_DIR.parent)).free
    vram = gpu_memory_info()
    return SystemInfo(
        ffmpegAvailable=caps["ffmpegFound"],
        ffmpegPath=resolve_ffmpeg(),
        rifeModelAvailable=engine is not None,
        rifeDevice=engine.device if engine else None,
        rifeProviders=engine.providers if engine else [],
        rifeTiling=engine.tiling if engine else False,
        nvencAvailable=caps["nvencUsable"],
        h264Encoder=h264,
        h265Encoder=h265,
        gpuName=_gpu_name(),
        cudaAvailable=bool(engine and engine.using_gpu),
        gpuVramTotalMb=vram["total"] if vram else None,
        gpuVramFreeMb=vram["free"] if vram else None,
        freeDiskBytes=free_disk,
        limits={
            "maxUploadBytes": MAX_UPLOAD_BYTES,
            "maxDurationSeconds": MAX_DURATION_SECONDS,
            "allowedExtensions": sorted(ALLOWED_VIDEO_EXTENSIONS),
            "jobTtlHours": JOB_TTL_HOURS,
        },
        presets={
            "dimensions": {k: (list(v) if v else None) for k, v in PRESET_DIMENSIONS.items()},
            "bitrateHintMbps": PRESET_BITRATE_HINT,
            "qualityCq": QUALITY_CQ,
        },
    )


@app.get("/api/system/model")
def model_status() -> dict:
    engine, engine_error = manager.engine_status()
    if engine is None:
        return {"loaded": False, "error": engine_error}
    return engine.describe()


@app.post("/api/probe")
async def probe_upload(file: UploadFile = File(...)) -> dict:
    """Quick metadata read for a file the browser is about to upload (validated on upload too)."""
    if not ffmpeg_available():
        raise HTTPException(status_code=503, detail="FFmpeg is not available on the server.")
    suffix = Path(_sanitize_filename(file.filename or "")).suffix.lower()
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'.")
    tmp_path = UPLOADS_DIR / f"probe-{new_job_id()}{suffix}"
    size = 0
    try:
        with tmp_path.open("wb") as handle:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File is larger than the upload limit.")
                handle.write(chunk)
        info = probe_video(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"original": info.as_dict()}


@app.post("/api/jobs", response_model=JobState)
async def create_job(
    file: UploadFile = File(...),
    settings: str = Form("{}"),
) -> JobState:
    if not ffmpeg_available():
        raise HTTPException(status_code=503, detail="FFmpeg is not available. Install it and restart the server.")

    try:
        parsed = json.loads(settings or "{}")
        job_settings = JobSettings.model_validate(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid settings: {exc}") from exc

    filename = _sanitize_filename(file.filename or "video.mp4")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or 'unknown'}'. Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}",
        )

    job_id = new_job_id()
    upload_path = UPLOADS_DIR / f"{job_id}{suffix}"

    size = 0
    try:
        with upload_path.open("wb") as handle:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_UPLOAD_BYTES / 1024**3:.1f} GB upload limit.",
                    )
                handle.write(chunk)
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if size == 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Validate the video before queueing the heavy work.
    try:
        info = probe_video(str(upload_path))
    except ProbeError as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from .pipeline import PipelineError, validate_settings

    try:
        validate_settings(info, job_settings)
    except PipelineError as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if info.duration > MAX_DURATION_SECONDS:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Video is {info.duration / 60:.1f} min; the limit is {MAX_DURATION_SECONDS / 60:.0f} min.",
        )

    try:
        choose_encoder(job_settings.codec, prefer_gpu=job_settings.prefer_gpu)
    except Exception as exc:  # noqa: BLE001
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"No usable {job_settings.codec} encoder: {exc}") from exc

    state = manager.create_job(
        job_id, job_settings, upload_path, source_name=Path(filename).stem, original=info.as_dict()
    )
    return state


@app.get("/api/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    job = _get_job_or_404(job_id)
    return job.snapshot()


@app.post("/api/jobs/{job_id}/cancel", response_model=JobState)
def cancel_job(job_id: str) -> JobState:
    job = _get_job_or_404(job_id)
    manager.cancel(job_id)
    return job.snapshot()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    if not manager.delete(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"deleted": True}


@app.get("/api/jobs/{job_id}/original")
def stream_original(job_id: str, request: Request) -> Response:
    job = _get_job_or_404(job_id)
    if not job.upload_path.exists():
        raise HTTPException(status_code=410, detail="Original file is no longer available.")
    return _range_response(job.upload_path, request, as_attachment=False, download_name=None)


@app.get("/api/jobs/{job_id}/preview")
def stream_preview(job_id: str, request: Request) -> Response:
    job = _get_job_or_404(job_id)
    if job.state.status != "done" or not job.output_path.exists():
        raise HTTPException(status_code=409, detail="Enhanced video is not ready yet.")
    return _range_response(job.output_path, request, as_attachment=False, download_name=None)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str, request: Request) -> Response:
    job = _get_job_or_404(job_id)
    if job.state.status != "done" or not job.output_path.exists():
        raise HTTPException(status_code=409, detail="Enhanced video is not ready yet.")
    info = job.state.original or {}
    stem = Path(_sanitize_filename(job.source_name or "video")).stem or "video"
    fps = job.state.enhanced.fps if job.state.enhanced else None
    suffix = f"_{fps:g}fps" if fps else ""
    download_name = f"{stem}_enhanced{suffix}.mp4"
    return _range_response(job.output_path, request, as_attachment=True, download_name=download_name)


@app.exception_handler(RifeError)
def _rife_error_handler(request: Request, exc: RifeError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# ------------------------------------------------------------------ desktop UI
# In the packaged desktop app this same process also serves the statically
# exported Next.js frontend, so there is a single origin for uploads, range
# requests and the File System Access API (localhost is a secure context, so the
# "save to folder" picker works). Registered last: every /api route above wins
# over this catch-all. Disabled in a source checkout so `next dev` stays the
# only UI server unless AVE_SERVE_UI=1 is set explicitly.
if SERVE_UI and UI_DIR.is_dir():
    _UI_ROOT = UI_DIR.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def _serve_ui(full_path: str) -> Response:
        candidate = (_UI_ROOT / full_path.lstrip("/")).resolve()
        if candidate.is_file() and _UI_ROOT in candidate.parents:
            headers = {}
            if "/_next/static/" in full_path.replace("\\", "/"):
                # Content-hashed build output: safe to cache forever.
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return FileResponse(candidate, headers=headers)
        index = _UI_ROOT / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="UI bundle not found.")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    logger.info("Serving UI bundle from %s", UI_DIR)
