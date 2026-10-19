import clsx from "clsx";
import { BookOpen, FlaskConical, ShieldAlert, Waves, type LucideIcon } from "lucide-react";
import { Fragment } from "react";
import type { Briefing, BriefingSection, RiskLevel } from "@/api/types";
import { CopyButton, ErrorNote, Skeleton } from "./ui";

const ICONS: Record<BriefingSection["id"], LucideIcon> = {
  what: BookOpen,
  affected: Waves,
  tests: FlaskConical,
  risk: ShieldAlert,
};

const RISK: Record<RiskLevel, { label: string; text: string; border: string; bg: string }> = {
  low: { label: "Low risk", text: "text-ok", border: "border-ok/40", bg: "bg-ok/10" },
  moderate: { label: "Moderate risk", text: "text-warn", border: "border-warn/40", bg: "bg-warn/10" },
  high: { label: "High risk", text: "text-bad", border: "border-bad/40", bg: "bg-bad/10" },
};

/** The API wraps names in backticks; set those in code style and leave the rest as prose. */
export function Prose({ text, className }: { text: string; className?: string }) {
  return (
    <p className={className}>
      {text.split("`").map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className="rounded bg-raised px-1 py-px font-mono text-[0.92em] text-ink">
            {part}
          </code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </p>
  );
}

interface Props {
  data: Briefing | undefined;
  loading: boolean;
  error: Error | null;
  onSelect: (fqn: string) => void;
  /** The pytest command from the Tests tab, offered next to the "which tests" paragraph. */
  testCommand?: string;
}

export function BriefingPanel({ data, loading, error, onSelect, testCommand }: Props) {
  if (error && !data) return <ErrorNote message={error.message} />;
  if (!data) {
    return (
      <div className="space-y-3" aria-busy={loading}>
        <Skeleton className="h-20" />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
    );
  }

  const risk = RISK[data.risk_level];
  return (
    <div className={clsx("space-y-5 transition-opacity", loading && "opacity-60")}>
      <div>
        <div className="label mb-1.5">In plain English</div>
        <div className={clsx("rounded-lg border-l-4 px-3 py-2.5", risk.border, risk.bg)}>
          <div className={clsx("text-xs font-semibold uppercase tracking-wide", risk.text)}>{risk.label}</div>
          <p className="mt-0.5 text-sm font-medium leading-snug text-ink">{data.headline}</p>
        </div>
      </div>

      {data.sections.map((section, index) => {
        const Icon = ICONS[section.id];
        return (
          <section key={section.id} aria-labelledby={`brief-${section.id}`}>
            <h3 id={`brief-${section.id}`} className="mb-1.5 flex items-center gap-2 text-sm font-semibold text-ink">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-raised text-accent">
                <Icon size={14} />
              </span>
              <span className="font-mono text-2xs text-faint">{index + 1}</span>
              {section.title}
            </h3>
            <Prose text={section.text} className="text-[13px] leading-relaxed text-soft" />

            {section.links.length > 0 && (
              <ul className="mt-2 space-y-1">
                {section.links.map((link) => (
                  <li key={link.fqn}>
                    <button
                      onClick={() => onSelect(link.fqn)}
                      className="group flex w-full items-baseline justify-between gap-3 rounded-md border border-line bg-surface px-2.5 py-1.5 text-left transition-colors hover:border-accent/50"
                    >
                      <span className="truncate font-mono text-xs font-semibold text-ink group-hover:text-accent">
                        {link.label}
                      </span>
                      <span className="shrink-0 text-2xs text-faint">{link.note}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {section.id === "tests" && testCommand && (
              <div className="mt-2">
                <CopyButton text={testCommand} label="Copy the pytest command" />
              </div>
            )}
          </section>
        );
      })}

      <p className="border-t border-line pt-3 text-2xs leading-relaxed text-faint">
        Every sentence here is computed from this repository&apos;s call graph, so it only says what Faultline
        actually found. It reads calls; it does not run your code.
      </p>
    </div>
  );
}
