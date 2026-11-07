import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SystemStatus from "@/components/SystemStatus";
import type { SystemInfo } from "@/lib/types";

const base: SystemInfo = {
  ffmpegAvailable: true,
  ffmpegPath: "ffmpeg",
  rifeModelAvailable: true,
  rifeDevice: "CUDA",
  rifeError: null,
  rifeProviders: ["CUDAExecutionProvider"],
  rifeTiling: false,
  nvencAvailable: true,
  h264Encoder: "h264_nvenc",
  h265Encoder: "hevc_nvenc",
  gpuName: "Test GPU",
  cudaAvailable: true,
  gpuVramTotalMb: 8192,
  gpuVramFreeMb: 7000,
  freeDiskBytes: 1_000_000,
  limits: {
    maxUploadBytes: 1,
    maxDurationSeconds: 1,
    allowedExtensions: [".mp4"],
    jobTtlHours: 4,
  },
  presets: { dimensions: {}, bitrateHintMbps: {}, qualityCq: {} },
};

describe("SystemStatus", () => {
  it("shows a connecting chip before system info arrives", () => {
    render(<SystemStatus system={null} />);
    expect(screen.getByText("connecting…")).toBeInTheDocument();
  });

  it("surfaces an AI engine failure instead of waiting forever", () => {
    render(<SystemStatus system={{ ...base, rifeDevice: null, rifeError: "CUDA init failed" }} />);

    const chip = screen.getByText("unavailable");
    expect(chip).toBeInTheDocument();
    expect(chip.parentElement).toHaveAttribute("title", "CUDA init failed");
  });

  it("shows the detected CPU encoder when NVENC is not usable", () => {
    render(
      <SystemStatus
        system={{
          ...base,
          nvencAvailable: false,
          h264Encoder: "libx264",
          rifeDevice: "CPU",
          cudaAvailable: false,
        }}
      />,
    );

    expect(screen.getByText("libx264")).toBeInTheDocument();
  });

  it("warns when no H.264 encoder was detected", () => {
    render(
      <SystemStatus
        system={{
          ...base,
          nvencAvailable: false,
          h264Encoder: null,
          rifeModelAvailable: false,
          rifeDevice: null,
        }}
      />,
    );

    expect(screen.getByText("unavailable")).toBeInTheDocument();
    expect(screen.getByText("no model")).toBeInTheDocument();
  });
});
