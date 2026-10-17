"use client";

import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 sm:p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-zinc-100">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function FieldLabel({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-2 flex items-baseline justify-between">
      <span className="text-xs font-medium uppercase tracking-wider text-zinc-400">{children}</span>
      {hint && <span className="text-[11px] text-zinc-500">{hint}</span>}
    </div>
  );
}

export function OptionButton({
  selected,
  onClick,
  title,
  description,
  disabled,
  badge,
}: {
  selected: boolean;
  onClick: () => void;
  title: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  badge?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      className={[
        "relative flex flex-col gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors",
        selected
          ? "border-sky-500 bg-sky-500/10 text-zinc-50"
          : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:border-zinc-600",
        disabled ? "cursor-not-allowed opacity-40 hover:border-zinc-800" : "",
      ].join(" ")}
    >
      <span className="text-sm font-medium">{title}</span>
      {description && <span className="text-[11px] leading-tight text-zinc-400">{description}</span>}
      {badge && (
        <span className="absolute right-2 top-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400">
          {badge}
        </span>
      )}
    </button>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 h-5 w-9 shrink-0 rounded-full border transition-colors ${
          checked ? "border-sky-500 bg-sky-500/70" : "border-zinc-700 bg-zinc-800"
        }`}
      >
        <span
          className={`block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
            checked ? "translate-x-4" : "translate-x-0.5"
          }`}
        />
      </button>
      <span className="text-xs">
        <span className="block text-zinc-200">{label}</span>
        {description && <span className="mt-0.5 block text-zinc-500">{description}</span>}
      </span>
    </label>
  );
}

export function Select({
  value,
  onChange,
  options,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-sky-500 disabled:opacity-50"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
