export type PresetName = "tiktok" | "reels" | "shorts" | "custom" | "original";
export type FitMode = "cover" | "contain" | "original";
export type CodecName = "h264" | "h265";
export type QualityName = "high" | "balanced" | "fast";

export interface VideoMetadata {
  width: number;
  height: number;
  fps: number;
  duration: number;
  sizeBytes: number;
  videoCodec: string;
  pixFmt: string | null;
  hasAudio: boolean;
  audioCodec: string | null;
  audioChannels: number | null;
  audioSampleRate: number | null;
  bitrate: number | null;
  nbFrames: number | null;
  container: string;
}

export interface JobSettings {
  targetFps: number;
  slowMotion: boolean;
  slowMotionSpeed: number;
  preset: PresetName;
  fitMode: FitMode;
  codec: CodecName;
  quality: QualityName;
  videoBitrateMbps: number | null;
  audioBitrate: string;
  preferGpu: boolean;
  customWidth: number | null;
  customHeight: number | null;
  /** Fraction of the output resolution the AI renders (1 = full, 0.75/0.5 = faster). */
  computeScale: number;
}

export type JobStatus =
  | "queued"
  | "validating"
  | "processing"
  | "cancelling"
  | "finalizing"
  | "done"
  | "error"
  | "cancelled";

export interface EnhancedInfo {
  width: number;
  height: number;
  fps: number;
  duration: number;
  sizeBytes: number;
  videoCodec: string;
  encoder: string;
  encoderKind: string;
  bitrate: number | null;
  interpolatedFrames: number;
  interpolationFactor: number;
}

export interface JobState {
  id: string;
  status: JobStatus;
  progress: number;
  stage: string;
  message: string;
  framesProcessed: number;
  totalFrames: number;
  processingFps: number;
  etaSeconds: number | null;
  elapsedSeconds: number;
  error: string | null;
  settings: JobSettings | null;
  original: VideoMetadata | null;
  enhanced: EnhancedInfo | null;
  device: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface SystemInfo {
  ffmpegAvailable: boolean;
  ffmpegPath: string | null;
  rifeModelAvailable: boolean;
  rifeDevice: string | null;
  rifeProviders: string[];
  rifeTiling: boolean;
  nvencAvailable: boolean;
  h264Encoder: string | null;
  h265Encoder: string | null;
  gpuName: string | null;
  cudaAvailable: boolean;
  gpuVramTotalMb: number | null;
  gpuVramFreeMb: number | null;
  freeDiskBytes: number;
  limits: {
    maxUploadBytes: number;
    maxDurationSeconds: number;
    allowedExtensions: string[];
    jobTtlHours: number;
  };
  presets: {
    dimensions: Record<string, [number, number] | null>;
    bitrateHintMbps: Record<string, number>;
    qualityCq: Record<string, number>;
  };
}

/** Reply of GET /api/update/check. `latest: null` = offline or already current. */
export interface UpdateCheck {
  current: string;
  latest: string | null;
  tag?: string;
  url?: string;
  sizeBytes?: number;
  notes?: string;
  publishedAt?: string;
  sha256?: string | null;
}

/** Progress of the background installer download (GET /api/update/status). */
export interface UpdateDownloadState {
  state: "idle" | "downloading" | "ready" | "error";
  tag: string | null;
  received: number;
  total: number;
  path: string | null;
  sha256_ok: boolean | null;
  error: string | null;
  percent: number | null;
}

export const DEFAULT_SETTINGS: JobSettings = {
  targetFps: 60,
  slowMotion: false,
  slowMotionSpeed: 0.5,
  preset: "tiktok",
  fitMode: "cover",
  codec: "h264",
  quality: "high",
  videoBitrateMbps: null,
  audioBitrate: "192k",
  preferGpu: true,
  customWidth: null,
  customHeight: null,
  computeScale: 1,
};
