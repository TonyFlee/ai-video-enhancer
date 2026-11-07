import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteJob,
  fetchSystem,
  getJob,
  probeFile,
  startUpdateDownload,
} from "@/lib/api";

function mockFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API helpers", () => {
  it("returns parsed system information", async () => {
    const fetchMock = mockFetch(
      new Response(JSON.stringify({ ffmpegAvailable: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchSystem()).resolves.toEqual({ ffmpegAvailable: true });
    expect(fetchMock).toHaveBeenCalledWith("/api/system", { cache: "no-store" });
  });

  it("turns FastAPI validation errors into readable messages", async () => {
    mockFetch(
      new Response(JSON.stringify({ detail: [{ msg: "fps too high" }, { msg: "bad size" }] }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(getJob("abc")).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "fps too high; bad size",
    });
  });

  it("keeps the HTTP status on API errors", async () => {
    mockFetch(new Response(JSON.stringify({ detail: "Job not found" }), { status: 404 }));

    const error = await getJob("missing").catch((err: unknown) => err);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).message).toBe("Job not found");
  });

  it("falls back to the status text when the error body is not JSON", async () => {
    mockFetch(new Response("<html>oops</html>", { status: 500, statusText: "Server Error" }));

    await expect(startUpdateDownload()).rejects.toMatchObject({
      status: 500,
      message: "500 Server Error",
    });
  });

  it("unwraps the original metadata from the probe response", async () => {
    const original = { width: 1920, height: 1080 };
    mockFetch(
      new Response(JSON.stringify({ original }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const file = new File(["video"], "clip.mp4", { type: "video/mp4" });

    await expect(probeFile(file)).resolves.toEqual(original);
  });

  it("treats a missing job as already deleted", async () => {
    mockFetch(new Response("", { status: 404 }));

    await expect(deleteJob("gone")).resolves.toBeUndefined();
  });

  it("surfaces other delete failures", async () => {
    mockFetch(new Response(JSON.stringify({ detail: "Disk is read-only" }), { status: 500 }));

    await expect(deleteJob("abc")).rejects.toThrow("Disk is read-only");
  });
});
