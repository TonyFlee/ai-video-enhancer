import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ComparePlayer from "@/components/ComparePlayer";

const playMock = vi.fn(() => Promise.resolve());
const pauseMock = vi.fn();

beforeEach(() => {
  playMock.mockClear();
  pauseMock.mockClear();
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: playMock,
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: pauseMock,
  });
});

function getVideos() {
  const videos = Array.from(document.querySelectorAll("video"));
  return { original: videos[0] as HTMLVideoElement, enhanced: videos[1] as HTMLVideoElement };
}

function setDuration(video: HTMLVideoElement, duration: number) {
  Object.defineProperty(video, "duration", { configurable: true, value: duration });
}

describe("ComparePlayer", () => {
  it("renders both streams with their labels", () => {
    render(<ComparePlayer originalSrc="/original.mp4" enhancedSrc="/enhanced.mp4" />);

    const { original, enhanced } = getVideos();
    expect(original).toHaveAttribute("src", "/original.mp4");
    expect(enhanced).toHaveAttribute("src", "/enhanced.mp4");
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(screen.getByText("Enhanced")).toBeInTheDocument();
  });

  it("plays and pauses both videos from the shared controls", async () => {
    const user = userEvent.setup();
    render(<ComparePlayer originalSrc="/original.mp4" enhancedSrc="/enhanced.mp4" />);

    await user.click(screen.getByRole("button", { name: "Play both videos" }));

    expect(playMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Pause both videos" })).toBeInTheDocument();
  });

  it("maps seeks by the duration ratio of the two clips", () => {
    render(<ComparePlayer originalSrc="/original.mp4" enhancedSrc="/enhanced.mp4" />);
    const { original, enhanced } = getVideos();
    setDuration(original, 10);
    setDuration(enhanced, 20);
    fireEvent.loadedMetadata(original);
    fireEvent.loadedMetadata(enhanced);

    fireEvent.change(screen.getByRole("slider", { name: "Seek both videos" }), {
      target: { value: "2" },
    });

    expect(original.currentTime).toBeCloseTo(2);
    expect(enhanced.currentTime).toBeCloseTo(4);
    expect(screen.getByText(/0:10/)).toBeInTheDocument();
  });

  it("applies the duration ratio to the enhanced playback rate", async () => {
    const user = userEvent.setup();
    render(<ComparePlayer originalSrc="/original.mp4" enhancedSrc="/enhanced.mp4" />);
    const { original, enhanced } = getVideos();
    setDuration(original, 10);
    setDuration(enhanced, 20);
    fireEvent.loadedMetadata(original);
    fireEvent.loadedMetadata(enhanced);

    await user.selectOptions(screen.getByRole("combobox", { name: "Playback speed" }), "0.5");

    expect(original.playbackRate).toBeCloseTo(0.5);
    expect(enhanced.playbackRate).toBeCloseTo(1);
  });

  it("toggles audio on both videos", async () => {
    const user = userEvent.setup();
    render(<ComparePlayer originalSrc="/original.mp4" enhancedSrc="/enhanced.mp4" />);
    const { original, enhanced } = getVideos();

    await user.click(screen.getByRole("button", { name: "Unmute" }));

    expect(original.muted).toBe(false);
    expect(enhanced.muted).toBe(false);
    expect(screen.getByRole("button", { name: "Mute" })).toBeInTheDocument();
  });

  it("stops both videos when either one ends", () => {
    render(<ComparePlayer originalSrc="/original.mp4" enhancedSrc="/enhanced.mp4" />);
    const { original } = getVideos();

    fireEvent.ended(original);

    expect(pauseMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Play both videos" })).toBeInTheDocument();
  });
});
