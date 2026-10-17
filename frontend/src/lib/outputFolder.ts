/**
 * Save enhanced videos straight into a folder the user picks (Chrome/Edge desktop).
 *
 * Uses the File System Access API: the user picks a directory once, we remember the
 * handle in IndexedDB, and every finished job is streamed into that folder with
 * progress — no 250 MB trip through the Downloads folder. Browsers without the API
 * (Firefox/Safari) fall back to the normal download button; nothing here is required
 * for the app to work.
 */

/** Minimal typings so we do not depend on the TS version shipping lib.dom FS Access. */
export interface FsWritable {
  write(data: Uint8Array): Promise<void>;
  truncate(size: number): Promise<void>;
  close(): Promise<void>;
}
export interface FsFileHandle {
  readonly name: string;
  createWritable(): Promise<FsWritable>;
}
export interface FsDirectoryHandle {
  readonly name: string;
  readonly kind: "directory";
  getFileHandle(name: string, options?: { create?: boolean }): Promise<FsFileHandle>;
  queryPermission(descriptor?: { mode?: "read" | "readwrite" }): Promise<PermissionState>;
  requestPermission(descriptor?: { mode?: "read" | "readwrite" }): Promise<PermissionState>;
}

interface DirectoryPickerWindow {
  showDirectoryPicker?: (options?: {
    id?: string;
    mode?: "read" | "readwrite";
  }) => Promise<FsDirectoryHandle>;
}

const DB_NAME = "ave-output-folder";
const STORE = "handles";
const KEY = "destination";

export function supportsFolderPicker(): boolean {
  return typeof window !== "undefined" && "showDirectoryPicker" in window;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function persistFolder(handle: FsDirectoryHandle | null): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    if (handle) tx.objectStore(STORE).put(handle, KEY);
    else tx.objectStore(STORE).delete(KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function loadFolder(): Promise<FsDirectoryHandle | null> {
  try {
    const db = await openDb();
    const handle = await new Promise<FsDirectoryHandle | null>((resolve, reject) => {
      const req = db.transaction(STORE, "readonly").objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve((req.result as FsDirectoryHandle) ?? null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return handle;
  } catch {
    return null;
  }
}

export async function pickFolder(): Promise<FsDirectoryHandle> {
  const picker = (window as DirectoryPickerWindow).showDirectoryPicker;
  if (!picker) throw new Error("This browser cannot pick folders (Chrome/Edge desktop only).");
  return picker({ id: "ave-output", mode: "readwrite" });
}

/** Re-ask for permission after a reload — handles survive, grants do not. */
export async function ensurePermission(handle: FsDirectoryHandle): Promise<boolean> {
  const opts = { mode: "readwrite" as const };
  if ((await handle.queryPermission(opts)) === "granted") return true;
  return (await handle.requestPermission(opts)) === "granted";
}

/**
 * Stream a URL into the folder, calling onProgress with 0..1 as chunks land.
 * Throws AbortError-style errors on failure so callers can fall back to a plain
 * browser download.
 */
export async function saveToFolder(
  url: string,
  fileName: string,
  dir: FsDirectoryHandle,
  onProgress?: (fraction: number) => void,
): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (HTTP ${res.status}).`);
  const total = Number(res.headers.get("content-length")) || 0;

  const file = await dir.getFileHandle(fileName, { create: true });
  const writable = await file.createWritable();
  try {
    const body = res.body;
    if (!body) {
      const buf = new Uint8Array(await res.arrayBuffer());
      await writable.write(buf);
      onProgress?.(1);
    } else {
      const reader = body.getReader();
      let received = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) {
          await writable.write(value);
          received += value.length;
          if (total > 0) onProgress?.(Math.min(1, received / total));
        }
      }
      onProgress?.(1);
    }
    await writable.close();
  } catch (err) {
    // Abandon the partial file rather than leaving a broken .mp4 behind.
    await writable.close().catch(() => {});
    try {
      await dir.getFileHandle(fileName);
      // getFileHandle has no delete on this API surface; truncate instead.
      const abort = await dir.getFileHandle(fileName, { create: true });
      const w2 = await abort.createWritable();
      await w2.truncate(0);
      await w2.close();
    } catch {
      /* ignore cleanup failures */
    }
    throw err;
  }
}

/** Prefer the server-provided filename; otherwise derive one from the job results. */
export function suggestedFileName(
  contentDisposition: string | null,
  fallback: string,
): string {
  const match = contentDisposition?.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : fallback;
}
