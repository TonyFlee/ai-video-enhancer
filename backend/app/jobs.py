"""In-memory background job manager with progress tracking, cancellation and cleanup."""
from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    JOB_TTL_HOURS,
    OUTPUTS_DIR,
    TMP_DIR,
    UPLOADS_DIR,
    ensure_dirs,
)
from .pipeline import Cancelled, PipelineError, PipelineResult, ProgressUpdate, VideoPipeline
from .probe import ProbeError, VideoInfo, probe_video
from .rife import RifeEngine, RifeError
from .schemas import EnhancedInfo, JobSettings, JobState

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    settings: JobSettings
    upload_path: Path
    output_path: Path
    state: JobState
    source_name: str = "video"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> JobState:
        with self._lock:
            self.state.elapsedSeconds = round(
                (self.finished_at or time.time()) - self.created_at, 1
            )
            return self.state.model_copy(deep=True)

    def update(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self.state, key, value)
            self.state.updatedAt = time.time()


class JobManager:
    def __init__(self) -> None:
        ensure_dirs()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._engine: RifeEngine | None = None
        self._engine_lock = threading.Lock()
        self._engine_error: str | None = None
        # RIFE is heavy; allow only one interpolation job at a time.
        self._process_semaphore = threading.Semaphore(1)

    # ------------------------------------------------------------------ engine
    def get_engine(self) -> RifeEngine:
        with self._engine_lock:
            if self._engine is None:
                from .config import RIFE_MODEL_PATH

                try:
                    self._engine = RifeEngine(RIFE_MODEL_PATH, prefer_gpu=True)
                except Exception as exc:
                    # Keep the last failure so /api/system/model can explain why
                    # the AI engine is unavailable instead of only logging it.
                    self._engine_error = str(exc)
                    raise
                self._engine_error = None
            return self._engine

    def engine_status(self) -> tuple[RifeEngine | None, str | None]:
        return self._engine, self._engine_error

    def warmup_async(self) -> None:
        def _warm() -> None:
            try:
                engine = self.get_engine()
                engine.warmup()
                logger.info("RIFE warmup complete (device=%s)", engine.device)
            except Exception as exc:  # noqa: BLE001
                self._engine_error = str(exc)
                logger.warning("RIFE warmup failed: %s", exc)

        threading.Thread(target=_warm, name="rife-warmup", daemon=True).start()

    # -------------------------------------------------------------------- jobs
    def create_job(
        self,
        job_id: str,
        settings: JobSettings,
        upload_path: Path,
        source_name: str = "video",
        original: dict | None = None,
    ) -> JobState:
        output_path = OUTPUTS_DIR / f"{job_id}.mp4"
        state = JobState(
            id=job_id,
            status="validating",
            stage="validating",
            message="Validating video",
            settings=settings,
            original=original,
            createdAt=time.time(),
            updatedAt=time.time(),
        )
        job = Job(id=job_id, settings=settings, upload_path=upload_path,
                  output_path=output_path, state=state, source_name=source_name)
        with self._lock:
            self._jobs[job_id] = job

        job.thread = threading.Thread(target=self._run_job, args=(job,), name=f"job-{job_id}", daemon=True)
        job.thread.start()
        return job.snapshot()

    def _run_job(self, job: Job) -> None:
        # Only one job may hold the GPU slot at a time. Acquire in short slices so a
        # cancel request is honoured while the job is still queued, instead of the
        # request having to wait for the running job to finish first.
        acquired = self._process_semaphore.acquire(blocking=False)
        try:
            if not acquired:
                job.update(status="queued", stage="queued", message="Waiting for another job to finish")
                while not job.cancel_event.is_set():
                    if self._process_semaphore.acquire(timeout=0.25):
                        acquired = True
                        break
            if not acquired:
                raise Cancelled()
            self._execute(job)
        except Cancelled:
            logger.info("Job %s cancelled", job.id)
            job.update(status="cancelled", stage="cancelled", message="Processing cancelled")
            self._cleanup_output(job)
        except (PipelineError, ProbeError, RifeError) as exc:
            logger.warning("Job %s failed: %s", job.id, exc)
            job.update(status="error", stage="error", error=str(exc), message=str(exc))
            self._cleanup_output(job)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s crashed", job.id)
            job.update(status="error", stage="error", error=f"Unexpected error: {exc}", message="Processing failed")
            self._cleanup_output(job)
        finally:
            job.finished_at = time.time()
            if acquired:
                self._process_semaphore.release()

    def _execute(self, job: Job) -> None:
        if job.cancel_event.is_set():
            raise Cancelled()
        info: VideoInfo = probe_video(str(job.upload_path))
        job.update(
            status="processing",
            stage="processing",
            message="Loading AI model",
            original=info.as_dict(),
            progress=0.0,
        )

        def on_progress(update: ProgressUpdate) -> None:
            if job.cancel_event.is_set():
                # A cancellation is in flight; keep the "cancelling" status until the
                # pipeline actually stops rather than flickering back to "processing".
                return
            progress = (update.processed / update.total * 100.0) if update.total else 0.0
            status = "processing"
            if update.stage == "processing":
                # Reserve the last 2% for finalising/muxing.
                progress = min(progress, 98.0)
            elif update.stage == "finalizing":
                status = "finalizing"
                progress = max(progress, 98.0)
            job.update(
                status=status,
                stage=update.stage,
                message=update.message,
                progress=round(progress, 2),
                framesProcessed=update.processed,
                totalFrames=update.total,
                processingFps=round(update.fps, 2),
                etaSeconds=round(update.eta, 1) if update.eta is not None else None,
            )

        engine = self.get_engine()
        job.update(device=engine.device)

        pipeline = VideoPipeline(engine)
        result: PipelineResult = pipeline.run(
            input_path=job.upload_path,
            output_path=job.output_path,
            settings=job.settings,
            info=info,
            cancel_event=job.cancel_event,
            on_progress=on_progress,
        )

        output_bytes = result.output_path.stat().st_size
        enhanced = EnhancedInfo(
            width=result.width,
            height=result.height,
            fps=round(result.fps, 3),
            duration=round(result.duration, 3),
            sizeBytes=output_bytes,
            videoCodec=job.settings.codec,
            encoder=result.encoder,
            encoderKind=result.encoder_kind,
            interpolatedFrames=result.interpolated_frames,
            interpolationFactor=round(result.interpolation_factor, 4),
            bitrate=result.bitrate,
        )
        job.update(
            status="done",
            stage="done",
            message="Enhancement complete",
            progress=100.0,
            etaSeconds=0.0,
            enhanced=enhanced,
            device=engine.device,
        )

    def _cleanup_output(self, job: Job) -> None:
        # Windows refuses to delete a file that still has an open handle, and the encoder
        # process can be a few milliseconds behind the kill() that cancels it. Retry
        # briefly instead of silently leaving a partial MP4 behind.
        for attempt in range(6):
            try:
                if not job.output_path.exists():
                    return
                job.output_path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                if attempt == 5:
                    logger.warning("Could not delete partial output %s", job.output_path)
                    return
                time.sleep(0.25 * (attempt + 1))

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        if job.state.status in ("done", "error", "cancelled"):
            return False
        job.cancel_event.set()
        job.update(status="cancelling", message="Cancelling...", stage="cancelling")
        return True

    def delete(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel_event.set()
        if job.thread and job.thread.is_alive():
            job.thread.join(timeout=10)
        self._cleanup_output(job)
        try:
            if job.upload_path.exists():
                job.upload_path.unlink()
        except OSError:
            pass
        with self._lock:
            self._jobs.pop(job_id, None)
        return True

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs.keys())

    def cleanup_expired(self, ttl_hours: float = JOB_TTL_HOURS) -> int:
        cutoff = time.time() - ttl_hours * 3600
        removed = 0
        for job_id in self.list_ids():
            job = self.get(job_id)
            if job is None:
                continue
            finished = job.finished_at or job.created_at
            if finished < cutoff and job.state.status in ("done", "error", "cancelled"):
                self.delete(job_id)
                removed += 1
        # Also remove orphan files from previous runs.
        for directory in (UPLOADS_DIR, OUTPUTS_DIR, TMP_DIR):
            if not directory.exists():
                continue
            for path in directory.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        if removed:
            logger.info("Cleaned up %d expired job artifact(s)", removed)
        return removed

    def purge_all_files(self) -> None:
        for directory in (UPLOADS_DIR, OUTPUTS_DIR, TMP_DIR):
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)

    def shutdown(self) -> None:
        """Cancel in-flight jobs and wait briefly for their FFmpeg children.

        Called from the FastAPI lifespan on server shutdown (including closing
        the desktop window) so a job cannot keep writing into storage after the
        app has exited.
        """
        with self._lock:
            jobs = list(self._jobs.values())
        active = [job for job in jobs if job.state.status not in ("done", "error", "cancelled")]
        for job in active:
            job.cancel_event.set()
        deadline = time.monotonic() + 20.0
        for job in active:
            thread = job.thread
            if thread and thread.is_alive():
                remaining = max(0.0, deadline - time.monotonic())
                thread.join(timeout=min(5.0, remaining))
        if active:
            logger.info("Cancelled %d active job(s) during shutdown", len(active))


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]
