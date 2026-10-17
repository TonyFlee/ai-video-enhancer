export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toFixed(value >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return "—";
  const total = Math.floor(seconds);
  const ms = Math.round((seconds - total) * 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const core = h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
  return `${core}.${String(ms).padStart(3, "0")}`;
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "calculating…";
  if (seconds <= 0.5) return "almost done";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function formatFps(fps: number | null | undefined): string {
  if (!fps) return "—";
  return Number.isInteger(fps) ? `${fps}` : fps.toFixed(2);
}

export function formatResolution(width?: number, height?: number): string {
  if (!width || !height) return "—";
  const ratio = (width / height).toFixed(2);
  const orient = height > width ? "vertical" : height === width ? "square" : "landscape";
  return `${width} × ${height} · ${ratio}:1 ${orient}`;
}

export function formatBitrate(bitsPerSecond: number | null | undefined): string {
  if (!bitsPerSecond) return "—";
  return `${(bitsPerSecond / 1_000_000).toFixed(1)} Mbps`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}
