import type { JobSettings, JobState, SystemInfo, UpdateCheck, UpdateDownloadState, VideoMetadata } from "./types";

async function parseError(response: Response): Promise<string> {
  try {
    const data = await response.json();
    const detail = data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (typeof item?.msg === "string" ? item.msg : JSON.stringify(item)))
        .join("; ");
    }
    return JSON.stringify(data);
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

/** HTTP error that keeps the status code so callers can treat 404 as "job is gone". */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function asApiError(response: Response): Promise<ApiError> {
  return new ApiError(await parseError(response), response.status);
}

export async function fetchSystem(): Promise<SystemInfo> {
  const response = await fetch("/api/system", { cache: "no-store" });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function probeFile(file: File): Promise<VideoMetadata> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/probe", { method: "POST", body });
  if (!response.ok) throw new Error(await parseError(response));
  const data = await response.json();
  return data.original as VideoMetadata;
}

export interface UploadHandlers {
  onProgress?: (percent: number) => void;
  signal?: AbortSignal;
}

/** Upload + create a processing job. Uses XHR so we can report real upload progress. */
export function createJob(
  file: File,
  settings: JobSettings,
  handlers: UploadHandlers = {},
): Promise<JobState> {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append("file", file);
    body.append("settings", JSON.stringify(settings));

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && handlers.onProgress) {
        handlers.onProgress((event.loaded / event.total) * 100);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as JobState);
      } else {
        const detail = (xhr.response as { detail?: unknown })?.detail;
        reject(new Error(typeof detail === "string" ? detail : `Upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error while uploading. Is the backend running?"));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));

    handlers.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(body);
  });
}

export async function getJob(jobId: string): Promise<JobState> {
  const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) throw await asApiError(response);
  return response.json();
}

export async function cancelJob(jobId: string): Promise<JobState> {
  const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  if (!response.ok) throw await asApiError(response);
  return response.json();
}

export async function deleteJob(jobId: string): Promise<void> {
  const response = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw new Error(await parseError(response));
}

export const originalUrl = (jobId: string) => `/api/jobs/${jobId}/original`;
export const previewUrl = (jobId: string) => `/api/jobs/${jobId}/preview`;
export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;

// ------------------------------------------------------------------ updates
export async function checkUpdate(): Promise<UpdateCheck> {
  const response = await fetch("/api/update/check", { cache: "no-store" });
  if (!response.ok) throw await asApiError(response);
  return response.json();
}

export async function startUpdateDownload(): Promise<UpdateDownloadState> {
  const response = await fetch("/api/update/download", { method: "POST" });
  if (!response.ok) throw await asApiError(response);
  return response.json();
}

export async function getUpdateStatus(): Promise<UpdateDownloadState> {
  const response = await fetch("/api/update/status", { cache: "no-store" });
  if (!response.ok) throw await asApiError(response);
  return response.json();
}

export async function installUpdate(): Promise<{ launched: boolean }> {
  const response = await fetch("/api/update/install", { method: "POST" });
  if (!response.ok) throw await asApiError(response);
  return response.json();
}
