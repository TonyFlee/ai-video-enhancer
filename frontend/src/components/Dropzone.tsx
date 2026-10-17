"use client";

import { useCallback, useRef, useState } from "react";
import { formatBytes } from "@/lib/format";

interface DropzoneProps {
  onFileSelected: (file: File) => void;
  disabled?: boolean;
  maxBytes?: number;
  allowedExtensions?: string[];
  file: File | null;
  error?: string | null;
}

export default function Dropzone({
  onFileSelected,
  disabled,
  maxBytes,
  allowedExtensions,
  file,
  error,
}: DropzoneProps) {
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const validate = useCallback(
    (candidate: File): string | null => {
      const ext = `.${candidate.name.split(".").pop()?.toLowerCase() ?? ""}`;
      if (allowedExtensions?.length && !allowedExtensions.includes(ext)) {
        return `Unsupported format "${ext}". Use ${allowedExtensions.join(", ")}.`;
      }
      if (maxBytes && candidate.size > maxBytes) {
        return `File is ${formatBytes(candidate.size)} — the limit is ${formatBytes(maxBytes)}.`;
      }
      if (candidate.size === 0) return "That file is empty.";
      return null;
    },
    [allowedExtensions, maxBytes],
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const candidate = files?.[0];
      if (!candidate) return;
      const problem = validate(candidate);
      setLocalError(problem);
      if (!problem) onFileSelected(candidate);
    },
    [onFileSelected, validate],
  );

  const shownError = localError ?? error ?? null;

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload a video"
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !disabled) {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled) handleFiles(event.dataTransfer.files);
        }}
        className={[
          "flex min-h-44 cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-6 text-center transition-colors",
          dragging ? "border-sky-400 bg-sky-500/5" : "border-zinc-700 bg-zinc-900/40 hover:border-zinc-500",
          disabled ? "cursor-not-allowed opacity-60" : "",
        ].join(" ")}
      >
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" className="text-zinc-400">
          <path d="M12 16V4m0 0L8 8m4-4 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <div>
          <p className="text-sm font-medium text-zinc-200">
            {file ? file.name : "Drop a video here or click to browse"}
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            {file
              ? `${formatBytes(file.size)} · click to choose a different video`
              : `MP4, MOV, MKV, AVI, WEBM · up to ${formatBytes(maxBytes ?? 0)}`}
          </p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={allowedExtensions?.join(",") ?? "video/*"}
          className="hidden"
          disabled={disabled}
          onChange={(event) => handleFiles(event.target.files)}
        />
      </div>
      {shownError && (
        <p className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-xs text-red-300">
          {shownError}
        </p>
      )}
    </div>
  );
}
