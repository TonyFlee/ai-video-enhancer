"use client";

import { useState } from "react";

import { Card, FieldLabel, OptionButton, Select, Toggle } from "@/components/ui";
import type { CodecName, FitMode, JobSettings, PresetName, QualityName, SystemInfo, VideoMetadata } from "@/lib/types";

interface SettingsPanelProps {
  settings: JobSettings;
  onChange: (settings: JobSettings) => void;
  metadata: VideoMetadata | null;
  system: SystemInfo | null;
  disabled?: boolean;
}

const FPS_OPTIONS = [
  { value: 60, title: "60 fps", description: "Universal smoothness · best platform support" },
  { value: 120, title: "120 fps", description: "Ultra-smooth · platforms may down-convert on upload" },
  { value: 30, title: "30 fps", description: "Keep 30 fps (slow motion only)" },
];

const PRESETS: Array<{ value: PresetName; title: string; description: string }> = [
  { value: "tiktok", title: "TikTok", description: "1080 × 1920 · 9:16 full-screen" },
  { value: "reels", title: "Instagram Reels", description: "1080 × 1920 · 9:16 full-screen" },
  { value: "shorts", title: "YouTube Shorts", description: "1080 × 1920 · 9:16 full-screen" },
  { value: "custom", title: "Custom size", description: "Any width × height you choose" },
  { value: "original", title: "Original size", description: "Keep the source resolution" },
];

const CUSTOM_SIZE_SHORTCUTS: Array<{ width: number; height: number; description: string }> = [
  { width: 720, height: 1280, description: "Fastest · roughly 2× quicker" },
  { width: 864, height: 1536, description: "Middle ground" },
  { width: 1080, height: 1920, description: "Platform native · slowest" },
];

const FIT_MODES: Array<{ value: FitMode; title: string; description: string }> = [
  { value: "cover", title: "Fill & crop", description: "Fill the frame, crop the overflow" },
  { value: "contain", title: "Fit & pad", description: "Letterbox with black bars" },
  { value: "original", title: "No scaling", description: "Leave the frame untouched" },
];

const QUALITIES: Array<{ value: QualityName; title: string; description: string }> = [
  { value: "high", title: "High", description: "Best quality · slower" },
  { value: "balanced", title: "Balanced", description: "Good quality · faster" },
  { value: "fast", title: "Fast", description: "Quick export · larger files" },
];

const PROCESSING_DETAIL: Array<{ value: number; title: string; description: string }> = [
  { value: 1, title: "Full quality", description: "AI renders at full output size · slowest" },
  { value: 0.75, title: "Faster", description: "AI renders at 75% · about 1.8× quicker" },
  { value: 0.5, title: "Fastest", description: "AI renders at 50% · about 3–4× quicker, slightly softer" },
];

const SIZE_MIN = 64;
const SIZE_MAX = 4096;

/** Friendly codec/encoder availability derived from a backend encoder name. */
export function describeEncoder(
  codec: CodecName,
  name: string | null | undefined,
): { available: boolean; label: string } {
  const codecLabel = codec === "h264" ? "H.264" : "H.265 / HEVC";
  if (!name) return { available: false, label: `${codecLabel} · unavailable` };
  const hardware = name.includes("nvenc");
  return {
    available: true,
    label: `${codecLabel} · ${hardware ? "NVENC GPU" : "CPU"}`,
  };
}

/** Parse a size input, tolerating empty/invalid text and clamping to the allowed range. */
function clampSize(raw: string, fallback: number): number {
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.round(value)));
}

/**
 * Number field that lets the user type freely and only clamps on blur/Enter,
 * so mid-edit values like "1" are not snapped back to the minimum.
 */
function SizeInput({
  label,
  value,
  disabled,
  onCommit,
}: {
  label: string;
  value: number;
  disabled?: boolean;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));

  const commit = () => {
    const nextValue = clampSize(draft, value);
    setDraft(String(nextValue));
    onCommit(nextValue);
  };

  return (
    <input
      type="number"
      inputMode="numeric"
      min={SIZE_MIN}
      max={SIZE_MAX}
      aria-label={label}
      disabled={disabled}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") commit();
      }}
      className="w-24 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-sky-500 disabled:opacity-50"
    />
  );
}

function fpsWarning(targetFps: number, metadata: VideoMetadata | null, slowMotion: boolean, speed: number): string | null {
  if (!metadata) return null;
  const effective = metadata.fps * (slowMotion ? speed : 1);
  const factor = targetFps / effective;
  if (factor < 1) {
    return `Source is ${metadata.fps.toFixed(2)} fps — choose a higher target or enable slow motion.`;
  }
  if (factor > 8) {
    return `${factor.toFixed(1)}× interpolation is heavy and may look synthetic.`;
  }
  return null;
}

export default function SettingsPanel({ settings, onChange, metadata, system, disabled }: SettingsPanelProps) {
  const update = <K extends keyof JobSettings>(key: K, value: JobSettings[K]) =>
    onChange({ ...settings, [key]: value });

  const selectPreset = (preset: PresetName) => {
    if (preset === "custom") {
      // Seed a sensible starting size on first selection, keep the user's own if set.
      onChange({
        ...settings,
        preset,
        customWidth: settings.customWidth ?? 720,
        customHeight: settings.customHeight ?? 1280,
      });
      return;
    }
    // Fixed presets must not be overridden by leftover custom dimensions.
    onChange({ ...settings, preset, customWidth: null, customHeight: null });
  };

  const customWidth = settings.customWidth ?? 720;
  const customHeight = settings.customHeight ?? 1280;

  const warning = fpsWarning(settings.targetFps, metadata, settings.slowMotion, settings.slowMotionSpeed);
  const bitrateHint = system?.presets.bitrateHintMbps[settings.preset] ?? 12;
  const h264 = describeEncoder("h264", system?.h264Encoder);
  const h265 = describeEncoder("h265", system?.h265Encoder);
  const selectedEncoder = settings.codec === "h265" ? h265 : h264;
  const noEncoder = system != null && !h264.available && !h265.available;

  return (
    <div className="space-y-4">
      <Card title="Output frame rate" subtitle="AI interpolation inserts real intermediate frames">
        <div className="grid grid-cols-3 gap-2">
          {FPS_OPTIONS.map((option) => (
            <OptionButton
              key={option.value}
              selected={settings.targetFps === option.value}
              onClick={() => update("targetFps", option.value)}
              title={option.title}
              description={option.description}
              disabled={disabled}
              badge={option.value === 120 ? "optional" : undefined}
            />
          ))}
        </div>
        {warning && <p className="mt-3 text-[11px] text-amber-300">{warning}</p>}
      </Card>

      <Card
        title="Processing detail"
        subtitle="How much work the AI does per frame — output size never changes"
      >
        <div className="grid grid-cols-3 gap-2">
          {PROCESSING_DETAIL.map((option) => (
            <OptionButton
              key={option.value}
              selected={Math.abs(settings.computeScale - option.value) < 1e-6}
              onClick={() => update("computeScale", option.value)}
              title={option.title}
              description={option.description}
              disabled={disabled}
              badge={option.value === 0.5 ? "fastest" : undefined}
            />
          ))}
        </div>
        <p className="mt-3 text-[11px] text-zinc-500">
          Faster modes let the AI work on a smaller frame and upscale the result — same
          output resolution and frame rate, with slightly softer detail on fast motion.
        </p>
      </Card>

      <Card title="Slow motion" subtitle="Stretch the timeline and synthesise extra frames">
        <Toggle
          checked={settings.slowMotion}
          onChange={(value) => update("slowMotion", value)}
          disabled={disabled}
          label="Enable smooth slow motion"
          description="Audio is time-stretched to stay in sync"
        />
        {settings.slowMotion && (
          <div className="mt-3 max-w-40">
            <FieldLabel hint="playback rate">Speed</FieldLabel>
            <Select
              value={String(settings.slowMotionSpeed)}
              onChange={(value) => update("slowMotionSpeed", Number(value))}
              disabled={disabled}
              options={[
                { value: "0.25", label: "0.25× (very slow)" },
                { value: "0.5", label: "0.5× (half speed)" },
                { value: "0.75", label: "0.75×" },
              ]}
            />
          </div>
        )}
      </Card>

      <Card title="Platform preset" subtitle="Frame size and orientation for the target app">
        <div className="grid grid-cols-2 gap-2">
          {PRESETS.map((preset) => (
            <OptionButton
              key={preset.value}
              selected={settings.preset === preset.value}
              onClick={() => selectPreset(preset.value)}
              title={preset.title}
              description={preset.description}
              disabled={disabled}
            />
          ))}
        </div>
        {settings.preset === "custom" && (
          <div className="mt-4 space-y-3">
            <FieldLabel hint="64–4096 px · odd values snap to even">Output size</FieldLabel>
            <div className="flex items-center gap-2">
              <SizeInput
                key={customWidth}
                label="Output width"
                value={customWidth}
                disabled={disabled}
                onCommit={(value) => update("customWidth", value)}
              />
              <span className="text-sm text-zinc-500">×</span>
              <SizeInput
                key={customHeight}
                label="Output height"
                value={customHeight}
                disabled={disabled}
                onCommit={(value) => update("customHeight", value)}
              />
              <span className="text-[11px] text-zinc-500">width × height</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {CUSTOM_SIZE_SHORTCUTS.map((size) => (
                <OptionButton
                  key={`${size.width}x${size.height}`}
                  selected={customWidth === size.width && customHeight === size.height}
                  onClick={() =>
                    onChange({ ...settings, customWidth: size.width, customHeight: size.height })
                  }
                  title={`${size.width} × ${size.height}`}
                  description={size.description}
                  disabled={disabled}
                />
              ))}
            </div>
            <p className="text-[11px] text-zinc-500">
              Interpolation cost scales with pixel count — a smaller frame finishes faster on the same GPU.
              A fixed preset cannot be changed while a job is already running.
            </p>
          </div>
        )}
        {settings.preset !== "original" && (
          <div className="mt-4">
            <FieldLabel hint={`${bitrateHint} Mbps recommended`}>Framing</FieldLabel>
            <div className="grid grid-cols-3 gap-2">
              {FIT_MODES.map((mode) => (
                <OptionButton
                  key={mode.value}
                  selected={settings.fitMode === mode.value}
                  onClick={() => update("fitMode", mode.value)}
                  title={mode.title}
                  description={mode.description}
                  disabled={disabled}
                />
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card title="Export" subtitle="Codec, quality and audio">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel>Video codec</FieldLabel>
            <Select
              value={settings.codec}
              onChange={(value) => update("codec", value as CodecName)}
              disabled={disabled}
              options={[
                {
                  value: "h264",
                  label: system ? h264.label : "H.264 · most compatible",
                  disabled: system != null && !h264.available,
                },
                {
                  value: "h265",
                  label: system ? h265.label : "H.265 / HEVC · smaller files",
                  disabled: system != null && !h265.available,
                },
              ]}
            />
            {noEncoder && (
              <p className="mt-2 text-[11px] text-red-300">
                No supported video encoder was detected. Check that FFmpeg is installed and on PATH.
              </p>
            )}
            {!noEncoder && system != null && !selectedEncoder.available && (
              <p className="mt-2 text-[11px] text-amber-300">
                {settings.codec === "h265" ? "H.265" : "H.264"} is not available in this FFmpeg build —
                choose the other codec.
              </p>
            )}
          </div>
          <div>
            <FieldLabel>Audio bitrate</FieldLabel>
            <Select
              value={settings.audioBitrate}
              onChange={(value) => update("audioBitrate", value)}
              disabled={disabled || !metadata?.hasAudio}
              options={["96k", "128k", "160k", "192k", "256k", "320k"].map((value) => ({
                value,
                label: value.toUpperCase() + " AAC",
              }))}
            />
          </div>
        </div>

        <div className="mt-4">
          <FieldLabel>Quality</FieldLabel>
          <div className="grid grid-cols-3 gap-2">
            {QUALITIES.map((quality) => (
              <OptionButton
                key={quality.value}
                selected={settings.quality === quality.value}
                onClick={() => update("quality", quality.value)}
                title={quality.title}
                description={quality.description}
                disabled={disabled}
              />
            ))}
          </div>
        </div>

        <div className="mt-4">
          <FieldLabel
            hint={settings.videoBitrateMbps ? `${settings.videoBitrateMbps} Mbps` : "constant quality (recommended)"}
          >
            Bitrate control
          </FieldLabel>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={2}
              max={40}
              step={1}
              disabled={disabled || settings.videoBitrateMbps === null}
              value={settings.videoBitrateMbps ?? bitrateHint}
              onChange={(event) => update("videoBitrateMbps", Number(event.target.value))}
              className="h-1.5 w-full cursor-pointer appearance-none rounded bg-zinc-800 accent-sky-500 disabled:opacity-40"
            />
            <button
              type="button"
              disabled={disabled}
              onClick={() =>
                update("videoBitrateMbps", settings.videoBitrateMbps === null ? bitrateHint : null)
              }
              className="shrink-0 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500"
            >
              {settings.videoBitrateMbps === null ? "Set manually" : "Use auto"}
            </button>
          </div>
        </div>

        <div className="mt-4 border-t border-zinc-800 pt-4">
          <Toggle
            checked={settings.preferGpu}
            onChange={(value) => update("preferGpu", value)}
            disabled={disabled || (system != null && !selectedEncoder.available)}
            label="Prefer GPU encoding (NVENC) when available"
            description={
              system == null
                ? "Falls back to CPU automatically"
                : selectedEncoder.available && selectedEncoder.label.includes("NVENC")
                  ? "Uses the dedicated hardware encoder; falls back to CPU automatically"
                  : "No NVENC encoder for this codec — CPU encoding will be used"
            }
          />
        </div>
      </Card>
    </div>
  );
}
