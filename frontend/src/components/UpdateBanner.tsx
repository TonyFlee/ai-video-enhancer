"use client";

/**
 * Update banner: checks GitHub Releases (via the backend) once on open and
 * offers "Update now" / "Later".
 *
 * Update now downloads the installer in the background (live progress), then
 * launches the regular Windows setup wizard - the app closes itself when the
 * installer needs to replace files, and the wizard's Finish page reopens it.
 * "Later" dismisses for this release only; a newer release shows the banner
 * again.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { checkUpdate, getUpdateStatus, installUpdate, startUpdateDownload } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { UpdateCheck, UpdateDownloadState } from "@/lib/types";

type Phase = "checking" | "hidden" | "available" | "downloading" | "ready" | "installing" | "error";

const DISMISS_KEY = "ave-dismissed-update";

export default function UpdateBanner() {
  const [phase, setPhase] = useState<Phase>("checking");
  const [info, setInfo] = useState<UpdateCheck | null>(null);
  const [dl, setDl] = useState<UpdateDownloadState | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // ------------------------------------------------------- open: run a check
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const [update, status] = await Promise.all([checkUpdate(), getUpdateStatus()]);
        if (cancelled) return;
        if (status.state === "downloading") {
          // The backend is still downloading (e.g. the UI was reloaded) - resume.
          setInfo(update);
          setDl(status);
          setPhase("downloading");
          return;
        }
        if (status.state === "ready" && update.latest) {
          setInfo(update);
          setDl(status);
          setPhase("ready");
          return;
        }
        const dismissed = window.localStorage.getItem(DISMISS_KEY);
        if (update.latest && dismissed !== update.tag) {
          setInfo(update);
          setPhase("available");
        } else {
          setPhase("hidden");
        }
      } catch {
        if (!cancelled) setPhase("hidden"); // offline / old backend: stay quiet
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  // ------------------------------------------------- download progress poll
  const beginDownload = useCallback(async () => {
    setMessage(null);
    setPhase("downloading");
    try {
      setDl(await startUpdateDownload());
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    if (phase !== "downloading") return;
    const tick = async () => {
      try {
        const state = await getUpdateStatus();
        setDl(state);
        if (state.state === "ready") {
          stopPolling();
          setPhase("ready");
        } else if (state.state === "error") {
          stopPolling();
          setMessage(state.error ?? "The download failed.");
          setPhase("error");
        }
      } catch {
        // Transient hiccup (the backend is alive or it isn't) - keep polling.
      }
    };
    void tick();
    pollRef.current = window.setInterval(tick, 1000);
    return stopPolling;
  }, [phase, stopPolling]);

  // ------------------------------------------------------- install / dismiss
  const beginInstall = useCallback(async () => {
    setMessage(null);
    try {
      await installUpdate();
      setPhase("installing");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }, []);

  const later = useCallback(() => {
    if (info?.tag) window.localStorage.setItem(DISMISS_KEY, info.tag);
    setPhase("hidden");
  }, [info]);

  if (phase === "checking" || phase === "hidden") return null;

  const primaryButton =
    "rounded-lg bg-sky-500 px-3.5 py-1.5 text-xs font-semibold text-sky-950 transition hover:bg-sky-400 disabled:opacity-50";
  const ghostButton =
    "rounded-lg border border-zinc-600 px-3.5 py-1.5 text-xs text-zinc-300 transition hover:border-zinc-400 hover:text-zinc-100";

  return (
    <div className="rounded-xl border border-sky-700/60 bg-sky-950/40 px-4 py-3.5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 rounded-md border border-sky-500/50 bg-sky-500/15 px-2 py-0.5 text-[10px] font-bold tracking-widest text-sky-300">
            UPDATE
          </span>
          <div className="min-w-0">
            {phase === "available" && (
              <p className="text-sm font-medium text-zinc-50" title={info?.notes}>
                Version {info?.latest} is available
                <span className="ml-2 font-normal text-zinc-400">
                  {info?.sizeBytes ? formatBytes(info.sizeBytes) : ""} · you have {info?.current}
                </span>
              </p>
            )}
            {phase === "downloading" && (
              <p className="text-sm font-medium text-zinc-50">
                Downloading update {dl?.tag ?? info?.latest}…
                <span className="ml-2 font-normal text-zinc-400">
                  {(dl?.received ?? 0) > 0 ? `${formatBytes(dl!.received)} / ${formatBytes(dl!.total)}` : "connecting"}
                </span>
              </p>
            )}
            {phase === "ready" && (
              <p className="text-sm font-medium text-zinc-50">
                Update {info?.latest} downloaded
                <span className="ml-2 font-normal text-zinc-400">
                  {dl?.sha256_ok ? "SHA-256 verified · " : ""}ready to install
                </span>
              </p>
            )}
            {phase === "installing" && (
              <p className="text-sm font-medium text-zinc-50">
                Setup is starting — finish the wizard to complete the update.
                <span className="ml-2 font-normal text-zinc-400">
                  The app closes during install and reopens from the Finish page.
                </span>
              </p>
            )}
            {phase === "error" && (
              <p className="text-sm font-medium text-red-300">
                {message ?? "Something went wrong."}
                <span className="ml-2 font-normal text-zinc-400">You can also download the installer from the website.</span>
              </p>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {phase === "available" && (
            <>
              <button type="button" onClick={() => void beginDownload()} className={primaryButton}>
                Update now
              </button>
              <button type="button" onClick={later} className={ghostButton}>
                Later
              </button>
            </>
          )}
          {phase === "ready" && (
            <>
              <button type="button" onClick={() => void beginInstall()} className={primaryButton}>
                Install &amp; restart
              </button>
              <button type="button" onClick={later} className={ghostButton}>
                Later
              </button>
            </>
          )}
          {phase === "error" && (
            <>
              <button type="button" onClick={() => void beginDownload()} className={primaryButton}>
                Try again
              </button>
              <button type="button" onClick={later} className={ghostButton}>
                Dismiss
              </button>
            </>
          )}
        </div>
      </div>

      {phase === "downloading" && (
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
          <div
            className="h-full rounded-full bg-sky-400 transition-[width] duration-500"
            style={{ width: `${dl?.percent ?? 0}%` }}
          />
        </div>
      )}
    </div>
  );
}
