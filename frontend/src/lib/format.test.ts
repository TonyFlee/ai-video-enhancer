import { describe, expect, it } from "vitest";

import {
  formatBitrate,
  formatBytes,
  formatDuration,
  formatEta,
  formatFps,
  formatNumber,
  formatResolution,
} from "@/lib/format";

describe("formatBytes", () => {
  it("returns a dash for missing or non-positive values", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(0)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });

  it("scales units and keeps one decimal for small values", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(10 * 1024 * 1024)).toBe("10.0 MB");
    expect(formatBytes(250 * 1024 * 1024)).toBe("250 MB");
  });
});

describe("formatDuration", () => {
  it("returns a dash for missing or negative durations", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(-0.5)).toBe("—");
  });

  it("formats minutes and hours with milliseconds", () => {
    expect(formatDuration(5.25)).toBe("0:05.250");
    expect(formatDuration(65.5)).toBe("1:05.500");
    expect(formatDuration(3661.125)).toBe("1:01:01.125");
  });
});

describe("formatEta", () => {
  it("handles unknown, imminent and short durations", () => {
    expect(formatEta(null)).toBe("calculating…");
    expect(formatEta(0.2)).toBe("almost done");
    expect(formatEta(9.1)).toBe("10s");
    expect(formatEta(65)).toBe("1m 5s");
  });

  it("switches to hours for long jobs", () => {
    expect(formatEta(3 * 3600 + 12 * 60)).toBe("3h 12m");
  });
});

describe("formatFps", () => {
  it("prints integers without decimals and rounds fractions", () => {
    expect(formatFps(60)).toBe("60");
    expect(formatFps(59.94)).toBe("59.94");
    expect(formatFps(0)).toBe("—");
  });
});

describe("formatResolution", () => {
  it("describes orientation and aspect ratio", () => {
    expect(formatResolution(1080, 1920)).toBe("1080 × 1920 · 0.56:1 vertical");
    expect(formatResolution(1920, 1080)).toBe("1920 × 1080 · 1.78:1 landscape");
    expect(formatResolution(1000, 1000)).toBe("1000 × 1000 · 1.00:1 square");
  });

  it("returns a dash when a dimension is missing", () => {
    expect(formatResolution(0, 1080)).toBe("—");
    expect(formatResolution(1920, undefined)).toBe("—");
  });
});

describe("formatBitrate and formatNumber", () => {
  it("converts bits per second to Mbps", () => {
    expect(formatBitrate(12_000_000)).toBe("12.0 Mbps");
    expect(formatBitrate(null)).toBe("—");
  });

  it("adds thousands separators", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
    expect(formatNumber(undefined)).toBe("—");
  });
});
