"use client";

import {
  formatBitrate,
  formatBytes,
  formatDuration,
  formatFps,
  formatNumber,
  formatResolution,
} from "@/lib/format";
import type { EnhancedInfo, VideoMetadata } from "@/lib/types";

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</dt>
      <dd className={`mt-0.5 text-sm ${accent ? "font-semibold text-sky-300" : "text-zinc-200"}`}>{value}</dd>
    </div>
  );
}

export function SourceStats({ original }: { original: VideoMetadata }) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <Stat label="Resolution" value={formatResolution(original.width, original.height)} />
      <Stat label="Frame rate" value={`${formatFps(original.fps)} fps`} />
      <Stat label="Duration" value={formatDuration(original.duration)} />
      <Stat label="File size" value={formatBytes(original.sizeBytes)} />
      <Stat label="Video codec" value={original.videoCodec.toUpperCase()} />
      <Stat
        label="Audio"
        value={original.hasAudio ? `${(original.audioCodec ?? "audio").toUpperCase()} · ${original.audioChannels ?? "?"}ch` : "none"}
      />
    </dl>
  );
}

export function EnhancedStats({ enhanced, original }: { enhanced: EnhancedInfo; original?: VideoMetadata | null }) {
  const fpsGain =
    original && original.fps > 0 ? `${(enhanced.fps / original.fps).toFixed(1)}× smoother` : undefined;
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <Stat label="Resolution" value={formatResolution(enhanced.width, enhanced.height)} accent />
      <Stat label="Frame rate" value={`${formatFps(enhanced.fps)} fps`} accent />
      <Stat label="Duration" value={formatDuration(enhanced.duration)} />
      <Stat label="File size" value={formatBytes(enhanced.sizeBytes)} />
      <Stat label="Encoder" value={enhanced.encoder} />
      <Stat
        label="AI frames added"
        value={`${formatNumber(enhanced.interpolatedFrames)} (${enhanced.interpolationFactor.toFixed(2)}×)`}
      />
      {fpsGain && <Stat label="Smoothness" value={fpsGain} />}
      {enhanced.bitrate ? <Stat label="Bitrate" value={formatBitrate(enhanced.bitrate)} /> : null}
    </dl>
  );
}
