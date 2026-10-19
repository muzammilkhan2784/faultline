import clsx from "clsx";
import { Check, Copy, Loader2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { RepoStatus } from "@/api/types";

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" className="fill-raised" />
      <circle cx="16" cy="16" r="11" fill="none" className="stroke-accent" strokeOpacity=".28" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="6.5" fill="none" className="stroke-accent" strokeOpacity=".5" strokeWidth="1.5" />
      <path
        d="M4 17h6l3-7 4 13 3-9 2 3h6"
        fill="none"
        className="stroke-accent"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const STATUS: Record<RepoStatus, { label: string; tone: string; busy: boolean }> = {
  pending: { label: "Queued", tone: "text-soft border-line bg-raised", busy: true },
  parsing: { label: "Parsing", tone: "text-info border-info/30 bg-info/10", busy: true },
  writing: { label: "Writing graph", tone: "text-warn border-warn/30 bg-warn/10", busy: true },
  ready: { label: "Ready", tone: "text-ok border-ok/30 bg-ok/10", busy: false },
  failed: { label: "Failed", tone: "text-bad border-bad/30 bg-bad/10", busy: false },
};

export function StatusPill({ status }: { status: RepoStatus }) {
  const s = STATUS[status];
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-semibold", s.tone)}>
      {s.busy ? <Loader2 size={11} className="animate-spin" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {s.label}
    </span>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "accent" | "ok";
}) {
  return (
    <div className="card animate-rise p-4">
      <div className="label">{label}</div>
      <div
        className={clsx(
          "mt-1.5 font-mono text-2xl font-semibold tracking-tight",
          tone === "accent" && "text-accent",
          tone === "ok" && "text-ok",
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-faint">{hint}</div>}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 1600);
    return () => clearTimeout(t);
  }, [done]);

  return (
    <button
      className="btn px-2.5 py-1 text-xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
        } catch {
          /* clipboard unavailable (insecure context): nothing useful to do */
        }
      }}
    >
      {done ? <Check size={13} className="text-ok" /> : <Copy size={13} />}
      {done ? "Copied" : label}
    </button>
  );
}

export function EmptyState({
  icon,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <div className="text-sm font-semibold text-ink">{title}</div>
      {children && <div className="mt-1.5 max-w-sm text-xs leading-relaxed text-soft">{children}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-md bg-raised", className)} />;
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">
      {message}
    </div>
  );
}

export function Badge({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: "muted" | "ok" | "info" | "warn" | "accent";
}) {
  const tones = {
    muted: "border-line text-faint",
    ok: "border-ok/30 bg-ok/10 text-ok",
    info: "border-info/30 bg-info/10 text-info",
    warn: "border-warn/30 bg-warn/10 text-warn",
    accent: "border-accent/30 bg-accent/10 text-accent",
  };
  return (
    <span className={clsx("inline-flex items-center rounded border px-1.5 py-px text-2xs font-semibold", tones[tone])}>
      {children}
    </span>
  );
}

/** A confidence value with a small bar: green when solid, red when a guess. */
export function ConfidenceBar({ value }: { value: number }) {
  const tone = value >= 0.9 ? "bg-ok" : value >= 0.6 ? "bg-warn" : "bg-bad";
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-8 overflow-hidden rounded-full bg-raised">
        <span className={clsx("block h-full rounded-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="w-9 font-mono text-xs tabular-nums text-soft">{value.toFixed(2)}</span>
    </span>
  );
}
