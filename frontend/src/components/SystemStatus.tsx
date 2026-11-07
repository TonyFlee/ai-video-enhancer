"use client";

import type { SystemInfo } from "@/lib/types";
import { formatBytes } from "@/lib/format";

function Chip({
  label,
  value,
  tone,
  title,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "muted";
  title?: string;
}) {
  const toneClass =
    tone === "ok"
      ? "border-emerald-500/40 text-emerald-300"
      : tone === "warn"
        ? "border-amber-500/40 text-amber-300"
        : "border-zinc-700 text-zinc-400";
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${toneClass}`}
    >
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span className="font-medium">{value}</span>
    </span>
  );
}

export default function SystemStatus({ system }: { system: SystemInfo | null }) {
  if (!system) {
    return <Chip label="engine" value="connecting…" tone="muted" />;
  }

  const aiValue = system.rifeError
    ? "unavailable"
    : (system.rifeDevice ?? (system.rifeModelAvailable ? "starting…" : "no model"));
  const aiTone = system.rifeError || !system.rifeModelAvailable ? "warn" : system.cudaAvailable ? "ok" : "warn";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Chip
        label="ffmpeg"
        value={system.ffmpegAvailable ? "ready" : "missing"}
        tone={system.ffmpegAvailable ? "ok" : "warn"}
      />
      <Chip
        label="ai device"
        value={aiValue}
        tone={aiTone}
        title={system.rifeError ?? undefined}
      />
      <Chip
        label="encoder"
        value={system.nvencAvailable ? "NVENC GPU" : (system.h264Encoder ?? "unavailable")}
        tone={system.nvencAvailable ? "ok" : system.h264Encoder ? "muted" : "warn"}
      />
      {system.gpuName && <Chip label="gpu" value={system.gpuName} tone="muted" />}
      <Chip label="free disk" value={formatBytes(system.freeDiskBytes)} tone="muted" />
    </div>
  );
}
