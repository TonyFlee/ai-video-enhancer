"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface ComparePlayerProps {
  originalSrc: string;
  enhancedSrc: string;
  originalLabel?: string;
  enhancedLabel?: string;
}

/**
 * Two synchronised players: play/pause/seek/mute is mirrored so both videos show the
 * same moment. The enhanced clip is the one with real interpolated frames, so the
 * difference in smoothness is visible when scrubbing or pausing.
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
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rate, setRate] = useState(1);

  const playerA = () => originalRef.current;
  const playerB = () => enhancedRef.current;

  const syncFrom = useCallback((source: HTMLVideoElement | null) => {
    if (!source || syncingRef.current) return;
    syncingRef.current = true;
    const other = source === playerA() ? playerB() : playerA();
    if (other && Math.abs(other.currentTime - source.currentTime) > 0.05) {
      other.currentTime = source.currentTime;
    }
    setCurrent(source.currentTime);
    setDuration(source.duration || 0);
    syncingRef.current = false;
  }, []);

  useEffect(() => {
    const a = playerA();
    const b = playerB();
    if (!a || !b) return;

    const onTime = () => syncFrom(a);
    const onMeta = () => setDuration(a.duration || 0);
    const onPlay = () => {
      setPlaying(true);
      void b.play().catch(() => undefined);
    };
    const onPause = () => {
      setPlaying(false);
      b.pause();
    };

    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
    };
  }, [syncFrom]);

  const togglePlay = () => {
    const a = playerA();
    const b = playerB();
    if (!a || !b) return;
    if (a.paused) {
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
    a.currentTime = value;
    b.currentTime = value;
    setCurrent(value);
  };

  const changeRate = (value: number) => {
    setRate(value);
    [playerA(), playerB()].forEach((player) => {
      if (player) player.playbackRate = value;
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
              <span>{formatClock(duration)}</span>
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

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
