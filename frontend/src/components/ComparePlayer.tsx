"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface ComparePlayerProps {
  originalSrc: string;
  enhancedSrc: string;
  originalLabel?: string;
  enhancedLabel?: string;
}

/** Clamp a playback rate to what browsers reliably support. */
function clampRate(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(16, Math.max(0.0625, value));
}

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Two synchronised players: play/pause/seek/mute is mirrored so both videos show
 * the same moment. The clips can have different durations (slow motion makes the
 * enhanced video longer), so positions are mapped by duration ratio rather than
 * by absolute seconds. The enhanced clip is the one with real interpolated
 * frames, so the difference in smoothness is visible when scrubbing or pausing.
 */
export default function ComparePlayer({
  originalSrc,
  enhancedSrc,
  originalLabel = "Original",
  enhancedLabel = "Enhanced",
}: ComparePlayerProps) {
  const originalRef = useRef<HTMLVideoElement>(null);
  const enhancedRef = useRef<HTMLVideoElement>(null);
  const syncingRef = useRef(false);
  const durationsRef = useRef({ original: 0, enhanced: 0 });
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [enhancedDuration, setEnhancedDuration] = useState(0);
  const [rate, setRate] = useState(1);

  const playerA = () => originalRef.current;
  const playerB = () => enhancedRef.current;

  /** enhanced seconds per original second (1 for a normal export, 2 for 0.5x slow-mo). */
  const durationRatio = useCallback(() => {
    const aDuration = playerA()?.duration || durationsRef.current.original;
    const bDuration = playerB()?.duration || durationsRef.current.enhanced;
    if (
      Number.isFinite(aDuration) &&
      Number.isFinite(bDuration) &&
      aDuration > 0 &&
      bDuration > 0
    ) {
      return bDuration / aDuration;
    }
    return 1;
  }, []);

  const applyRates = useCallback(() => {
    const ratio = durationRatio();
    const a = playerA();
    const b = playerB();
    if (a) a.playbackRate = clampRate(rate);
    if (b) b.playbackRate = clampRate(rate * ratio);
  }, [durationRatio, rate]);

  const syncFrom = useCallback(
    (source: HTMLVideoElement | null) => {
      if (!source || syncingRef.current) return;
      syncingRef.current = true;
      const a = playerA();
      const b = playerB();
      const ratio = durationRatio();

      if (source === b && a) {
        const target = ratio > 0 ? source.currentTime / ratio : source.currentTime;
        if (Math.abs(a.currentTime - target) > 0.05) a.currentTime = target;
        setCurrent(target);
      } else {
        if (b) {
          const target = source.currentTime * ratio;
          if (Math.abs(b.currentTime - target) > 0.05) b.currentTime = target;
        }
        setCurrent(source.currentTime);
      }
      setDuration(a?.duration || source.duration || 0);
      syncingRef.current = false;
    },
    [durationRatio],
  );

  useEffect(() => {
    const a = playerA();
    const b = playerB();
    if (!a || !b) return;

    const readDurations = () => {
      durationsRef.current = {
        original: Number.isFinite(a.duration) ? a.duration : 0,
        enhanced: Number.isFinite(b.duration) ? b.duration : 0,
      };
      setDuration(durationsRef.current.original);
      setEnhancedDuration(durationsRef.current.enhanced);
      applyRates();
    };
    const onTime = () => syncFrom(a);
    const onPlay = () => {
      setPlaying(true);
      applyRates();
      void b.play().catch(() => undefined);
    };
    const onPause = () => {
      setPlaying(false);
      b.pause();
    };
    const onEnded = () => {
      // Either stream ending means the comparison is over; pause both instead
      // of letting the other one keep running (or spin at the end).
      setPlaying(false);
      a.pause();
      b.pause();
      if (Number.isFinite(a.duration)) setCurrent(a.duration);
      if (Number.isFinite(a.duration)) a.currentTime = a.duration;
      if (Number.isFinite(b.duration)) b.currentTime = b.duration;
    };

    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", readDurations);
    a.addEventListener("durationchange", readDurations);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onEnded);
    b.addEventListener("loadedmetadata", readDurations);
    b.addEventListener("durationchange", readDurations);
    b.addEventListener("ended", onEnded);

    // Metadata may already be available when the effect runs (cached videos).
    readDurations();

    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", readDurations);
      a.removeEventListener("durationchange", readDurations);
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onEnded);
      b.removeEventListener("loadedmetadata", readDurations);
      b.removeEventListener("durationchange", readDurations);
      b.removeEventListener("ended", onEnded);
    };
  }, [applyRates, syncFrom]);

  const togglePlay = () => {
    const a = playerA();
    const b = playerB();
    if (!a || !b) return;
    if (a.paused) {
      applyRates();
      void a.play().catch(() => undefined);
      void b.play().catch(() => undefined);
      setPlaying(true);
    } else {
      a.pause();
      b.pause();
      setPlaying(false);
    }
  };

  const seek = (value: number) => {
    const a = playerA();
    const b = playerB();
    if (!a || !b) return;
    const ratio = durationRatio();
    a.currentTime = value;
    b.currentTime = Math.min(value * ratio, b.duration || value * ratio);
    setCurrent(value);
  };

  const changeRate = (value: number) => {
    setRate(value);
    const ratio = durationRatio();
    [playerA(), playerB()].forEach((player, index) => {
      if (player) player.playbackRate = clampRate(index === 0 ? value : value * ratio);
    });
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    [playerA(), playerB()].forEach((player) => {
      if (player) player.muted = next;
    });
  };

  const toggleFullscreen = (element: HTMLElement | null) => {
    if (!element) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void element.requestFullscreen();
  };

  const progressPercent = duration > 0 ? (current / duration) * 100 : 0;
  const slowMotionRatio = duration > 0 && enhancedDuration > 0 ? enhancedDuration / duration : 1;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        <figure className="overflow-hidden rounded-lg border border-zinc-800 bg-black">
          <video
            ref={originalRef}
            src={originalSrc}
            muted
            playsInline
            preload="metadata"
            className="aspect-[9/16] w-full bg-black object-contain"
            onClick={togglePlay}
          />
          <figcaption className="flex items-center justify-between border-t border-zinc-800 bg-zinc-900 px-3 py-2 text-xs">
            <span className="text-zinc-400">{originalLabel}</span>
            <button
              type="button"
              onClick={() => toggleFullscreen(originalRef.current?.parentElement ?? null)}
              className="text-zinc-500 hover:text-zinc-300"
            >
              Fullscreen
            </button>
          </figcaption>
        </figure>

        <figure className="overflow-hidden rounded-lg border border-sky-500/30 bg-black">
          <video
            ref={enhancedRef}
            src={enhancedSrc}
            muted
            playsInline
            preload="metadata"
            className="aspect-[9/16] w-full bg-black object-contain"
            onClick={togglePlay}
          />
          <figcaption className="flex items-center justify-between border-t border-zinc-800 bg-zinc-900 px-3 py-2 text-xs">
            <span className="text-sky-300">{enhancedLabel}</span>
            <button
              type="button"
              onClick={() => toggleFullscreen(enhancedRef.current?.parentElement ?? null)}
              className="text-zinc-500 hover:text-zinc-300"
            >
              Fullscreen
            </button>
          </figcaption>
        </figure>
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={togglePlay}
            className="flex h-9 w-9 items-center justify-center rounded-full border border-zinc-700 text-zinc-200 hover:border-sky-500 hover:text-sky-300"
            aria-label={playing ? "Pause both videos" : "Play both videos"}
          >
            {playing ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="4" width="4" height="16" rx="1" />
                <rect x="14" y="4" width="4" height="16" rx="1" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M7 4.5v15l13-7.5-13-7.5Z" />
              </svg>
            )}
          </button>

          <div className="flex-1">
            <input
              type="range"
              min={0}
              max={duration || 0}
              step={0.01}
              value={current}
              onChange={(event) => seek(Number(event.target.value))}
              className="h-1.5 w-full cursor-pointer appearance-none rounded bg-zinc-800 accent-sky-500"
              aria-label="Seek both videos"
              style={{
                background: `linear-gradient(to right, #0ea5e9 ${progressPercent}%, #27272a ${progressPercent}%)`,
              }}
            />
            <div className="mt-1 flex justify-between text-[11px] text-zinc-500">
              <span>{formatClock(current)}</span>
              <span>
                {formatClock(duration)}
                {slowMotionRatio > 1.01 ? ` · enhanced ${formatClock(enhancedDuration)}` : ""}
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={toggleMute}
            className="rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-zinc-500"
          >
            {muted ? "Unmute" : "Mute"}
          </button>

          <select
            value={String(rate)}
            onChange={(event) => changeRate(Number(event.target.value))}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-300"
            aria-label="Playback speed"
          >
            {[0.25, 0.5, 1, 2].map((value) => (
              <option key={value} value={value}>
                {value}×
              </option>
            ))}
          </select>
        </div>
        <p className="mt-2 text-[11px] text-zinc-500">
          Playback is synchronised. Slow the speed down or step frame by frame to see the interpolated frames.
        </p>
      </div>
    </div>
  );
}
