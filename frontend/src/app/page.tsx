"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ComparePlayer from "@/components/ComparePlayer";
import Dropzone from "@/components/Dropzone";
import { EnhancedStats, SourceStats } from "@/components/MetaGrid";
import ProgressPanel from "@/components/ProgressPanel";
import SettingsPanel from "@/components/SettingsPanel";
import SystemStatus from "@/components/SystemStatus";
import { Card } from "@/components/ui";
import {
  ApiError,
  cancelJob,
  createJob,
  deleteJob,
  downloadUrl,
  fetchSystem,
  getJob,
  originalUrl,
  previewUrl,
  probeFile,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import {
  ensurePermission,
  loadFolder,
  pickFolder,
  persistFolder,
  saveToFolder,
  supportsFolderPicker,
  type FsDirectoryHandle,
} from "@/lib/outputFolder";
import { DEFAULT_SETTINGS, type JobSettings, type JobState, type SystemInfo, type VideoMetadata } from "@/lib/types";

type Phase = "idle" | "analyzing" | "ready" | "uploading" | "processing" | "done" | "error" | "cancelled";

export default function Home() {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [metadata, setMetadata] = useState<VideoMetadata | null>(null);
  const [settings, setSettings] = useState<JobSettings>(DEFAULT_SETTINGS);
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [outFolder, setOutFolder] = useState<FsDirectoryHandle | null>(null);
  const [saving, setSaving] = useState(false);
  const [savePercent, setSavePercent] = useState<number | null>(null);
  // Feature-detected after mount: the File System Access API only exists in the
  // browser, so deciding during render would make the server HTML and the first
  // client render differ (React hydration mismatch).
  const [supportsFolders, setSupportsFolders] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<number | null>(null);
  const jobIdRef = useRef<string | null>(null);

  // -------------------------------------------------- remembered output folder
  useEffect(() => {
    setSupportsFolders(supportsFolderPicker());
    let cancelled = false;
    void loadFolder().then((handle) => {
      if (!cancelled) setOutFolder(handle);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const chooseOutputFolder = useCallback(async () => {
    try {
      const handle = await pickFolder();
      if ((await ensurePermission(handle)) === false) {
        setError("Folder access was denied — the browser download folder will be used.");
        return null;
      }
      setOutFolder(handle);
      await persistFolder(handle);
      setError(null);
      return handle;
    } catch (err) {
      // The user closing the picker dialog is not an error worth showing.
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Could not open the folder picker.");
      }
      return null;
    }
  }, []);

  const clearOutputFolder = useCallback(() => {
    setOutFolder(null);
    void persistFolder(null);
  }, []);

  // ------------------------------------------------------------- save result
  const handleSave = useCallback(async () => {
    if (!job) return;
    let dir = outFolder;
    if (dir && !(await ensurePermission(dir))) {
      // Permission lapsed (browser restart): ask again, or drop the folder.
      dir = (await chooseOutputFolder()) ?? null;
    } else if (!dir) {
      dir = await chooseOutputFolder();
    }
    if (!dir) return; // no folder chosen: the plain Download link still works

    const base =
      job.enhanced != null
        ? `enhanced_${Math.round(job.enhanced.fps)}fps_${job.enhanced.width}x${job.enhanced.height}.mp4`
        : `enhanced_${job.id}.mp4`;
    setSaving(true);
    setSavePercent(0);
    try {
      await saveToFolder(downloadUrl(job.id), base, dir, (fraction) =>
        setSavePercent(Math.round(fraction * 100)),
      );
      setSavePercent(null);
    } catch (err) {
      setSavePercent(null);
      setError(
        `Could not save into "${dir.name}" (${err instanceof Error ? err.message : "unknown error"}). ` +
          "Use Download MP4 instead.",
      );
    } finally {
      setSaving(false);
    }
  }, [job, outFolder, chooseOutputFolder]);

  // ------------------------------------------------------------ system info
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const info = await fetchSystem();
        if (!cancelled) setSystem(info);
      } catch {
        if (!cancelled) setError("Cannot reach the backend. Start it with `uvicorn app.main:app --port 8000`.");
      }
    };
    void load();
    const timer = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // -------------------------------------------------------- job status poll
  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const startPolling = useCallback(
    (jobId: string) => {
      stopPolling();
      pollRef.current = window.setInterval(async () => {
        try {
          const state = await getJob(jobId);
          setJob(state);
          if (state.status === "done") {
            setPhase("done");
            stopPolling();
          } else if (state.status === "error") {
            setError(state.error ?? "Processing failed.");
            setPhase("error");
            stopPolling();
          } else if (state.status === "cancelled") {
            setPhase("cancelled");
            stopPolling();
          }
        } catch (pollError) {
          if (pollError instanceof ApiError && pollError.status === 404) {
            // The job no longer exists (backend restarted or the job expired).
            // Drop the stale card and return to a state where the selected file can
            // simply be processed again, instead of showing a dead progress panel.
            stopPolling();
            setJob(null);
            setPhase("idle");
            setError("This job is no longer available (the server restarted or the job expired). Upload the video again.");
            return;
          }
          setError(pollError instanceof Error ? pollError.message : "Lost connection to the backend.");
          stopPolling();
        }
      }, 700);
    },
    [stopPolling],
  );

  // ------------------------------------------------------------ file choice
  const handleFile = useCallback(
    async (selected: File) => {
      setError(null);
      setJob(null);
      setMetadata(null);
      setPhase("analyzing");
      setFile(selected);
      try {
        const info = await probeFile(selected);
        setMetadata(info);
        // Sensible defaults based on the source: 60 fps unless the source is already 60+.
        setSettings((previous) => ({
          ...previous,
          targetFps: info.fps >= 60 ? 120 : 60,
        }));
        setPhase("ready");
      } catch (probeError) {
        setError(probeError instanceof Error ? probeError.message : "Could not read this video.");
        setPhase("error");
      }
    },
    [],
  );

  const busy = phase === "uploading" || phase === "processing" || phase === "analyzing";
  const active = phase === "uploading" || phase === "processing";

  // ------------------------------------------------------------------ start
  const startProcessing = async () => {
    if (!file || !metadata) return;
    setError(null);
    setPhase("uploading");
    setUploadPercent(0);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const state = await createJob(file, settings, {
        signal: controller.signal,
        onProgress: (percent) => setUploadPercent(percent),
      });
      setJob(state);
      setUploadPercent(null);
      jobIdRef.current = state.id;
      setPhase("processing");
      startPolling(state.id);
    } catch (uploadError) {
      setUploadPercent(null);
      if (uploadError instanceof DOMException && uploadError.name === "AbortError") {
        setPhase("cancelled");
        return;
      }
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
      setPhase("error");
    }
  };

  // ----------------------------------------------------------------- cancel
  const handleCancel = async () => {
    setCancelling(true);
    try {
      if (phase === "uploading") {
        abortRef.current?.abort();
        setPhase("cancelled");
        return;
      }
      if (job) {
        // Ask the backend to stop, then let polling observe the terminal "cancelled"
        // state so the UI does not claim success while the engine is still winding down.
        const state = await cancelJob(job.id);
        setJob(state);
      }
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Could not cancel the job.");
    } finally {
      setCancelling(false);
    }
  };

  const reset = async () => {
    stopPolling();
    const currentId = job?.id;
    setJob(null);
    setFile(null);
    setMetadata(null);
    setError(null);
    setUploadPercent(null);
    setPhase("idle");
    if (currentId) {
      try {
        await deleteJob(currentId);
      } catch {
        /* ignore cleanup failures */
      }
    }
  };

  const estimate = useMemo(() => {
    if (!metadata) return null;
    const speed = settings.slowMotion ? settings.slowMotionSpeed : 1;
    const factor = settings.targetFps / (metadata.fps * speed);
    const outputDuration = metadata.duration / speed;
    const frames = Math.round((metadata.nbFrames ?? metadata.duration * metadata.fps) * factor);
    return { factor, outputDuration, frames };
  }, [metadata, settings]);

  const presetDimensions =
    settings.preset === "custom"
      ? ([settings.customWidth ?? 720, settings.customHeight ?? 1280] as [number, number])
      : (system?.presets.dimensions[settings.preset] ?? null);
  const showCompare = phase === "done" && job?.status === "done";
  const showProgress = phase === "uploading" || phase === "processing" || job !== null;

  // Warn when another app (a game, another AI tool) is holding most of the VRAM:
  // on a 4 GB card that forces tiling and can make interpolation many times slower.
  const totalVramMb = system?.gpuVramTotalMb ?? 0;
  const freeVramMb = system?.gpuVramFreeMb ?? 0;
  const gpuCrowded =
    system?.cudaAvailable === true && totalVramMb > 0 && freeVramMb / totalVramMb < 0.45;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-50 sm:text-2xl">AI Video FPS Enhancer</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Real RIFE frame interpolation for TikTok, Reels and Shorts — 30/60 → 60/120 fps.
          </p>
        </div>
        <SystemStatus system={system} />
      </header>

      {error && (
        <div className="flex items-start justify-between gap-4 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3">
          <p className="text-sm text-red-300">{error}</p>
          <button type="button" onClick={() => setError(null)} className="text-xs text-red-300/70 hover:text-red-200">
            Dismiss
          </button>
        </div>
      )}

      <main className="grid flex-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* ------------------------------------------------------------ left */}
        <div className="space-y-6">
          <Card
            title="1 · Source video"
            subtitle="The original file is never modified and is deleted after the job expires"
            action={
              (file || metadata) && (
                <button
                  type="button"
                  onClick={reset}
                  disabled={active}
                  className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
                >
                  Clear
                </button>
              )
            }
          >
            <Dropzone
              onFileSelected={(selected) => void handleFile(selected)}
              disabled={busy}
              file={file}
              maxBytes={system?.limits.maxUploadBytes}
              allowedExtensions={system?.limits.allowedExtensions}
            />

            {phase === "analyzing" && <p className="mt-3 text-xs text-zinc-400">Reading video metadata…</p>}

            {metadata && (
              <div className="mt-4 space-y-3 border-t border-zinc-800 pt-4">
                <SourceStats original={metadata} />
                {estimate && (
                  <p className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-[11px] text-zinc-400">
                    Planned output: {estimate.factor.toFixed(2)}× interpolation →{" "}
                    {presetDimensions ? `${presetDimensions[0]}×${presetDimensions[1]}` : `${metadata.width}×${metadata.height}`}{" "}
                    at {settings.targetFps} fps, {estimate.frames.toLocaleString()} frames,{" "}
                    {formatDuration(estimate.outputDuration)} long.
                  </p>
                )}
              </div>
            )}
          </Card>

          <Card title="2 · Enhancement settings" subtitle={busy ? "Locked while processing" : undefined}>
            <SettingsPanel
              settings={settings}
              onChange={setSettings}
              metadata={metadata}
              system={system}
              disabled={busy}
            />
          </Card>

          {showProgress && (
            <ProgressPanel
              job={job}
              uploadPercent={uploadPercent}
              onCancel={() => void handleCancel()}
              cancelling={cancelling}
            />
          )}

          {!showCompare && (
            <>
              {supportsFolders && (
                <div className="flex items-center justify-between rounded-xl border border-zinc-800 px-4 py-3 text-xs">
                  <span className="text-zinc-400">
                    Save enhanced videos to:{" "}
                    <span className="font-medium text-zinc-200">
                      {outFolder ? outFolder.name : "my browser's download folder"}
                    </span>
                  </span>
                  <span className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => void chooseOutputFolder()}
                      disabled={busy}
                      className="rounded-lg border border-zinc-700 px-3 py-1.5 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
                    >
                      {outFolder ? "Change folder" : "Choose folder…"}
                    </button>
                    {outFolder && (
                      <button
                        type="button"
                        onClick={clearOutputFolder}
                        disabled={busy}
                        className="rounded-lg px-2 text-zinc-500 hover:text-zinc-300 disabled:opacity-40"
                      >
                        Use downloads
                      </button>
                    )}
                  </span>
                </div>
              )}

              {gpuCrowded && (
                <p className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-4 py-3 text-xs text-amber-300">
                  The GPU is busy — another app is using{" "}
                  {formatBytes((totalVramMb - freeVramMb) * 1024 * 1024)} of{" "}
                  {formatBytes(totalVramMb * 1024 * 1024)} VRAM. Close it and this job can run
                  several times faster.
                </p>
              )}

              <button
                type="button"
                onClick={() => void startProcessing()}
                disabled={!file || !metadata || busy}
                className="w-full rounded-xl bg-sky-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
              >
                {phase === "uploading"
                  ? `Uploading… ${uploadPercent?.toFixed(0) ?? 0}%`
                  : phase === "processing"
                    ? "Enhancing…"
                    : `Enhance to ${settings.targetFps} fps`}
              </button>
            </>
          )}
        </div>

        {/* ----------------------------------------------------------- right */}
        <div className="space-y-6">
          {showCompare && job && (
            <Card
              title="3 · Original vs enhanced"
              subtitle="Synchronised playback — compare smoothness"
              action={
                <div className="flex items-center gap-2">
                  <a
                    href={downloadUrl(job.id)}
                    className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500"
                  >
                    Download
                  </a>
                  <button
                    type="button"
                    onClick={() => void handleSave()}
                    disabled={saving}
                    className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-60"
                  >
                    {saving
                      ? `Saving… ${savePercent !== null ? `${savePercent}%` : ""}`
                      : outFolder
                        ? `Save to ${outFolder.name}`
                        : "Save to folder…"}
                  </button>
                </div>
              }
            >
              <ComparePlayer
                originalSrc={originalUrl(job.id)}
                enhancedSrc={previewUrl(job.id)}
                originalLabel={`Original · ${metadata?.fps.toFixed(0) ?? "?"} fps`}
                enhancedLabel={`Enhanced · ${job.enhanced?.fps.toFixed(0) ?? "?"} fps`}
              />
              {job.original && (
                <div className="mt-5 grid gap-5 border-t border-zinc-800 pt-4 lg:grid-cols-2">
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">Original</h3>
                    <SourceStats original={job.original} />
                  </div>
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">Enhanced</h3>
                    {job.enhanced && <EnhancedStats enhanced={job.enhanced} original={job.original} />}
                  </div>
                </div>
              )}
            </Card>
          )}

          {!showCompare && (
            <Card title="How it works" subtitle="No fake progress, no duplicated frames">
              <ol className="space-y-3 text-sm text-zinc-400">
                <li>
                  <span className="font-medium text-zinc-200">1. FFmpeg decodes</span> every frame and applies the
                  platform framing (1080 × 1920 crop/pad).
                </li>
                <li>
                  <span className="font-medium text-zinc-200">2. RIFE runs on your GPU</span> via ONNX Runtime CUDA,
                  synthesising genuinely new in-between frames.
                </li>
                <li>
                  <span className="font-medium text-zinc-200">3. NVENC / x264 encodes</span> an H.264 or H.265 MP4 and
                  re-attaches the original audio in sync.
                </li>
              </ol>
              <div className="mt-4 grid grid-cols-2 gap-3 border-t border-zinc-800 pt-4 text-[11px] text-zinc-500">
                <span>Upload limit: {formatBytes(system?.limits.maxUploadBytes)}</span>
                <span>Max duration: {system ? Math.round(system.limits.maxDurationSeconds / 60) : "—"} min</span>
                <span>Encoder: {system?.h264Encoder ?? "—"}</span>
                <span>Auto-delete: {system?.limits.jobTtlHours ?? "—"} h</span>
              </div>
            </Card>
          )}

          {showCompare && (
            <button
              type="button"
              onClick={reset}
              className="w-full rounded-xl border border-zinc-700 px-4 py-3 text-sm text-zinc-300 hover:border-zinc-500"
            >
              Enhance another video
            </button>
          )}
        </div>
      </main>

      <footer className="border-t border-zinc-900 pt-4 text-[11px] text-zinc-600">
        Frames are interpolated locally — files stay on this machine and are removed automatically after{" "}
        {system?.limits.jobTtlHours ?? 4} hours.
      </footer>
    </div>
  );
}

function formatDuration(seconds: number): string {
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
