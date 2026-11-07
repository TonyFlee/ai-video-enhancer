import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import SettingsPanel, { describeEncoder } from "@/components/SettingsPanel";
import { DEFAULT_SETTINGS, type JobSettings, type SystemInfo, type VideoMetadata } from "@/lib/types";

const metadata: VideoMetadata = {
  width: 1920,
  height: 1080,
  fps: 30,
  duration: 10,
  sizeBytes: 1_000_000,
  videoCodec: "h264",
  pixFmt: "yuv420p",
  hasAudio: true,
  audioCodec: "aac",
  audioChannels: 2,
  audioSampleRate: 48_000,
  bitrate: 8_000_000,
  nbFrames: 300,
  container: "mp4",
};

const system: SystemInfo = {
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
  freeDiskBytes: 100_000_000_000,
  limits: {
    maxUploadBytes: 4_000_000_000,
    maxDurationSeconds: 3600,
    allowedExtensions: [".mp4"],
    jobTtlHours: 24,
  },
  presets: {
    dimensions: { tiktok: [1080, 1920] },
    bitrateHintMbps: { tiktok: 12, custom: 14, original: 16 },
    qualityCq: { high: 19, balanced: 23, fast: 27 },
  },
};

function renderPanel(
  settings: Partial<JobSettings> = {},
  options: { system?: SystemInfo | null; metadata?: VideoMetadata | null } = {},
) {
  const onChange = vi.fn();
  render(
    <SettingsPanel
      settings={{ ...DEFAULT_SETTINGS, ...settings }}
      onChange={onChange}
      metadata={options.metadata === undefined ? metadata : options.metadata}
      system={options.system === undefined ? system : options.system}
    />,
  );
  return onChange;
}

describe("describeEncoder", () => {
  it("marks missing encoders as unavailable", () => {
    expect(describeEncoder("h264", null)).toEqual({
      available: false,
      label: "H.264 · unavailable",
    });
  });

  it("distinguishes NVENC from CPU encoders", () => {
    expect(describeEncoder("h264", "h264_nvenc").label).toBe("H.264 · NVENC GPU");
    expect(describeEncoder("h265", "libx265").label).toBe("H.265 / HEVC · CPU");
  });
});

describe("SettingsPanel", () => {
  it("labels encoders from the detected backend encoders", () => {
    renderPanel();
    expect(screen.getByRole("option", { name: "H.264 · NVENC GPU" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "H.265 / HEVC · NVENC GPU" })).toBeInTheDocument();
  });

  it("disables a codec that this FFmpeg build cannot encode", () => {
    renderPanel({}, { system: { ...system, h265Encoder: null } });

    expect(screen.getByRole("option", { name: "H.265 / HEVC · unavailable" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "H.264 · NVENC GPU" })).toBeEnabled();
  });

  it("warns when no video encoder is available at all", () => {
    renderPanel({}, { system: { ...system, h264Encoder: null, h265Encoder: null } });

    expect(screen.getByText(/No supported video encoder was detected/)).toBeInTheDocument();
  });

  it("explains CPU fallback for codecs without NVENC", () => {
    renderPanel({}, { system: { ...system, h264Encoder: "libx264", h265Encoder: "libx265" } });

    expect(screen.getByRole("option", { name: "H.264 · CPU" })).toBeInTheDocument();
    expect(screen.getByText(/CPU encoding will be used/)).toBeInTheDocument();
  });

  it("passes the selected codec to the parent", async () => {
    const user = userEvent.setup();
    const onChange = renderPanel();

    await user.selectOptions(screen.getAllByRole("combobox")[0], "h265");

    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_SETTINGS, codec: "h265" });
  });

  it("warns when the target FPS is below the source rate", () => {
    renderPanel({ targetFps: 30 }, { metadata: { ...metadata, fps: 60 } });
    expect(screen.getByText(/Source is 60.00 fps/)).toBeInTheDocument();
  });

  it("warns about very large interpolation factors", () => {
    renderPanel({ targetFps: 120 }, { metadata: { ...metadata, fps: 10 } });
    expect(screen.getByText(/12.0× interpolation is heavy/)).toBeInTheDocument();
  });

  it("seeds custom dimensions when the custom preset is chosen", async () => {
    const user = userEvent.setup();
    const onChange = renderPanel({ preset: "tiktok" });

    await user.click(screen.getByRole("button", { name: /Custom size/ }));

    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_SETTINGS,
      preset: "custom",
      customWidth: 720,
      customHeight: 1280,
    });
  });

  it("clears custom dimensions when a fixed preset is chosen", async () => {
    const user = userEvent.setup();
    const onChange = renderPanel({ preset: "custom", customWidth: 800, customHeight: 800 });

    await user.click(screen.getByRole("button", { name: /TikTok/ }));

    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_SETTINGS,
      preset: "tiktok",
      customWidth: null,
      customHeight: null,
    });
  });

  it("disables audio bitrate selection for silent videos", () => {
    renderPanel({}, { metadata: { ...metadata, hasAudio: false } });
    const audioSelect = screen.getAllByRole("combobox")[1];
    expect(audioSelect).toBeDisabled();
  });

  it("reveals the slow-motion speed selector only when enabled", () => {
    const { unmount } = render(
      <SettingsPanel
        settings={{ ...DEFAULT_SETTINGS, slowMotion: false }}
        onChange={vi.fn()}
        metadata={metadata}
        system={system}
      />,
    );
    expect(screen.queryByRole("option", { name: "0.5× (half speed)" })).not.toBeInTheDocument();
    unmount();

    render(
      <SettingsPanel
        settings={{ ...DEFAULT_SETTINGS, slowMotion: true }}
        onChange={vi.fn()}
        metadata={metadata}
        system={system}
      />,
    );
    expect(screen.getByRole("option", { name: "0.5× (half speed)" })).toBeInTheDocument();
  });
});
