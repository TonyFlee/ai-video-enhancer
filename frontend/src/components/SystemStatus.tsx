"use client";

import type { SystemInfo } from "@/lib/types";
import { formatBytes } from "@/lib/format";

function Chip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "muted";
}) {
  const toneClass =
    tone === "ok"
      ? "border-emerald-500/40 text-emerald-300"
      : tone === "warn"
        ? "border-amber-500/40 text-amber-300"
        : "border-zinc-700 text-zinc-400";
  return (
    <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${toneClass}`}>
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span className="font-medium">{value}</span>
    </span>
  );
}

export default function SystemStatus({ system }: { system: SystemInfo | null }) {
  if (!system) {
    return <Chip label="engine" value="connecting…" tone="muted" />;
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Chip
        label="ffmpeg"
        value={system.ffmpegAvailable ? "ready" : "missing"}
        tone={system.ffmpegAvailable ? "ok" : "warn"}
      />
      <Chip
        label="ai device"
        value={system.rifeDevice ?? (system.rifeModelAvailable ? "loading…" : "no model")}
        tone={system.rifeModelAvailable ? (system.cudaAvailable ? "ok" : "warn") : "warn"}
      />
      <Chip
        label="encoder"
        value={system.nvencAvailable ? "NVENC GPU" : (system.h264Encoder ?? "cpu")}
        tone={system.nvencAvailable ? "ok" : "muted"}
      />
      {system.gpuName && <Chip label="gpu" value={system.gpuName} tone="muted" />}
      <Chip label="free disk" value={formatBytes(system.freeDiskBytes)} tone="muted" />
    </div>
  );
}
