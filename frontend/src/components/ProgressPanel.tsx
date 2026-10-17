"use client";

import { Card } from "@/components/ui";
import { formatEta, formatNumber } from "@/lib/format";
import type { JobState } from "@/lib/types";

const STAGE_LABEL: Record<string, string> = {
  queued: "Queued",
  validating: "Validating",
  processing: "Interpolating frames",
  cancelling: "Cancelling",
  finalizing: "Finalising export",
  done: "Complete",
  error: "Failed",
  cancelled: "Cancelled",
};

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-zinc-500">{label}</span>
      <span className="font-medium text-zinc-200">{value}</span>
    </div>
  );
}

export default function ProgressPanel({
  job,
  uploadPercent,
  onCancel,
  cancelling,
}: {
  job: JobState | null;
  uploadPercent?: number | null;
  onCancel?: () => void;
  cancelling?: boolean;
}) {
  const active = job && !["done", "error", "cancelled"].includes(job.status);
  const cancellingNow = Boolean(cancelling) || job?.status === "cancelling";
  const uploading = uploadPercent !== null && uploadPercent !== undefined && !job;

  const percent = uploading ? uploadPercent! : (job?.progress ?? 0);
  const label = uploading
    ? "Uploading video"
    : job
      ? (STAGE_LABEL[job.status] ?? job.stage)
      : "Preparing";

  return (
    <Card
      title="Processing"
      subtitle={job?.message || (uploading ? "Sending the file to the local engine" : "Waiting for a video")}
      action={
        active || uploading ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancellingNow}
            className="rounded-lg border border-red-500/40 px-3 py-1.5 text-xs text-red-300 transition-colors hover:bg-red-500/10 disabled:opacity-50"
          >
            {cancellingNow ? "Cancelling…" : "Cancel"}
          </button>
        ) : null
      }
    >
      <div className="space-y-3">
        <div>
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="text-zinc-300">{label}</span>
            <span className="font-mono text-zinc-400">{percent.toFixed(1)}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-800">
            <div
              className={`h-full rounded-full transition-[width] duration-300 ${
                job?.status === "error"
                  ? "bg-red-500"
                  : job?.status === "cancelled" || job?.status === "cancelling"
                    ? "bg-zinc-500"
                    : "bg-sky-500"
              }`}
              style={{ width: `${Math.min(Math.max(percent, 0), 100)}%` }}
            />
          </div>
        </div>

        {job && (
          <div className="grid gap-2 border-t border-zinc-800 pt-3">
            <Row
              label="Frames"
              value={`${formatNumber(job.framesProcessed)} / ${job.totalFrames ? formatNumber(job.totalFrames) : "—"}`}
            />
            <Row
              label="Interpolation speed"
              value={job.processingFps ? `${job.processingFps.toFixed(2)} frames/s` : "—"}
            />
            <Row label="Estimated remaining" value={active ? formatEta(job.etaSeconds) : "—"} />
            <Row label="Elapsed" value={formatEta(job.elapsedSeconds)} />
            <Row label="AI device" value={job.device ?? "—"} />
          </div>
        )}
      </div>
    </Card>
  );
}
