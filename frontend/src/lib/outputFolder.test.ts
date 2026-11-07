import { afterEach, describe, expect, it, vi } from "vitest";

import {
  saveToFolder,
  suggestedFileName,
  supportsFolderPicker,
  type FsDirectoryHandle,
  type FsWritable,
} from "@/lib/outputFolder";

function makeDirectory(writable: FsWritable) {
  const handle = {
    name: "out",
    kind: "directory" as const,
    getFileHandle: vi.fn(async () => ({ name: "clip.mp4", createWritable: async () => writable })),
    queryPermission: vi.fn(async () => "granted" as PermissionState),
    requestPermission: vi.fn(async () => "granted" as PermissionState),
  };
  return handle as FsDirectoryHandle & typeof handle;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("supportsFolderPicker", () => {
  it("is false when the browser has no directory picker", () => {
    expect(supportsFolderPicker()).toBe(false);
  });

  it("is true when showDirectoryPicker exists", () => {
    Object.defineProperty(window, "showDirectoryPicker", {
      configurable: true,
      value: vi.fn(),
    });
    expect(supportsFolderPicker()).toBe(true);
    Reflect.deleteProperty(window, "showDirectoryPicker");
  });
});

describe("suggestedFileName", () => {
  it("prefers the Content-Disposition filename", () => {
    expect(suggestedFileName('attachment; filename="enhanced.mp4"', "fallback.mp4")).toBe(
      "enhanced.mp4",
    );
    expect(suggestedFileName("attachment; filename=plain.mp4", "fallback.mp4")).toBe(
      "plain.mp4",
    );
  });

  it("falls back when the header is missing", () => {
    expect(suggestedFileName(null, "fallback.mp4")).toBe("fallback.mp4");
  });
});

describe("saveToFolder", () => {
  it("streams the response into the chosen file and reports progress", async () => {
    const write = vi.fn(async () => undefined);
    const close = vi.fn(async () => undefined);
    const writable: FsWritable = { write, truncate: vi.fn(async () => undefined), close };
    const dir = makeDirectory(writable);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("abcdef", { status: 200, headers: { "Content-Length": "6" } })),
    );
    const progress: number[] = [];

    await saveToFolder("/api/jobs/1/download", "clip.mp4", dir, (value) => progress.push(value));

    expect(dir.getFileHandle).toHaveBeenCalledWith("clip.mp4", { create: true });
    expect(write).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(1);
    expect(progress.at(-1)).toBe(1);
  });

  it("rejects failed downloads before touching the file", async () => {
    const dir = makeDirectory({
      write: vi.fn(async () => undefined),
      truncate: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));

    await expect(saveToFolder("/x", "clip.mp4", dir)).rejects.toThrow(
      "Download failed (HTTP 500).",
    );
    expect(dir.getFileHandle).not.toHaveBeenCalled();
  });

  it("truncates a partial file when writing fails", async () => {
    const truncate = vi.fn(async () => undefined);
    const mainWritable: FsWritable = {
      write: vi.fn(async () => {
        throw new Error("disk full");
      }),
      truncate: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
    };
    const cleanupWritable: FsWritable = {
      write: vi.fn(async () => undefined),
      truncate,
      close: vi.fn(async () => undefined),
    };
    const getFileHandle = vi
      .fn()
      .mockResolvedValueOnce({ name: "clip.mp4", createWritable: async () => mainWritable })
      .mockResolvedValue({ name: "clip.mp4", createWritable: async () => cleanupWritable });
    const dir = {
      name: "out",
      kind: "directory" as const,
      getFileHandle,
      queryPermission: vi.fn(async () => "granted" as PermissionState),
      requestPermission: vi.fn(async () => "granted" as PermissionState),
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response("abcdef", { status: 200 })));

    await expect(saveToFolder("/x", "clip.mp4", dir)).rejects.toThrow("disk full");

    expect(truncate).toHaveBeenCalledWith(0);
  });
});
