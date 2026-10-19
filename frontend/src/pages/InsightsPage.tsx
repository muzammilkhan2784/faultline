import { AlertTriangle, ArrowRight, Flame, RefreshCcw, Skull } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useCycles, useDeadCode, useHotspots, useRepo } from "@/api/hooks";
import type { ResolutionBreakdown } from "@/api/types";
import { EmptyState, ErrorNote, Skeleton, StatCard } from "@/components/ui";
import { displayName, formatInt, formatPercent, formatSeconds, splitFqn } from "@/lib/format";

function Section({
  title,
  icon,
  subtitle,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="card animate-rise overflow-hidden">
      <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
        <span className="text-accent">{icon}</span>
        <h2 className="text-sm font-semibold">{title}</h2>
        {subtitle && <span className="ml-auto text-xs text-faint">{subtitle}</span>}
      </header>
      {children}
    </section>
  );
}

const SEGMENTS: { key: keyof ResolutionBreakdown; label: string; color: string; note: string }[] = [
  { key: "resolved_exact", label: "Exact", color: "bg-ok", note: "a single unambiguous target" },
  { key: "resolved_inferred", label: "Inferred", color: "bg-info", note: "type heuristic, star import, alias" },
  { key: "resolved_ambiguous", label: "Ambiguous", color: "bg-warn", note: "several candidates, low confidence" },
  { key: "dropped_external", label: "External", color: "bg-faint", note: "stdlib / third-party, not modelled" },
  { key: "dropped_builtin", label: "Builtin", color: "bg-line", note: "len(), print()…" },
  { key: "dropped_too_ambiguous", label: "Too vague", color: "bg-bad/70", note: "more than 10 candidates" },
  { key: "unresolved", label: "Unresolved", color: "bg-bad", note: "could not be determined" },
];

/** The honest self-assessment: where every call site in the repo ended up. */
function ResolutionBar({ r }: { r: ResolutionBreakdown }) {
  const total = r.call_sites_total || 1;
  return (
    <div className="p-4">
      <div className="flex h-3 overflow-hidden rounded-full bg-raised" role="img" aria-label="Call-site resolution breakdown">
        {SEGMENTS.map((s) => {
          const value = r[s.key] as number;
          return value > 0 ? (
            <div key={s.key} className={s.color} style={{ width: `${(value / total) * 100}%` }} title={`${s.label}: ${formatInt(value)}`} />
          ) : null;
        })}
      </div>
      <ul className="mt-4 grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4">
        {SEGMENTS.map((s) => (
          <li key={s.key} className="flex items-start gap-2">
            <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-sm ${s.color}`} />
            <span className="min-w-0">
              <span className="flex items-baseline gap-2">
                <span className="text-xs font-semibold">{s.label}</span>
                <span className="font-mono text-xs text-soft">{formatInt(r[s.key] as number)}</span>
              </span>
              <span className="block text-2xs text-faint">{s.note}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-4 rounded-lg bg-raised px-3 py-2 text-xs leading-relaxed text-soft">
        Of the <b className="text-ink">{formatInt(r.internal_call_sites)}</b> calls that could target code in
        this repo, <b className="text-ink">{formatPercent(r.internal_resolution_rate)}</b> were resolved and{" "}
        <b className="text-ink">{formatPercent(r.internal_exact_rate)}</b> exactly. A tool claiming 100% for
        Python is guessing.
      </p>
    </div>
  );
}

export function InsightsPage() {
  const { repoId = "" } = useParams();
  const repo = useRepo(repoId);
  const cycles = useCycles(repoId);
  const hotspots = useHotspots(repoId, 25);
  const dead = useDeadCode(repoId);
  const stats = repo.data?.stats;
  const explore = (fqn: string) => `/repos/${repoId}?fqn=${encodeURIComponent(fqn)}`;

  if (repo.isLoading) return <div className="p-6"><Skeleton className="h-64" /></div>;
  if (repo.error) return <div className="p-6"><ErrorNote message={repo.error.message} /></div>;
  if (repo.data?.status !== "ready" || !stats) {
    return <EmptyState title="Insights appear once analysis finishes" />;
  }

  return (
    <div className="mx-auto h-full max-w-6xl space-y-5 overflow-y-auto px-5 py-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Modules" value={formatInt(stats.counts.modules)} hint={`${formatInt(stats.counts.classes)} classes`} />
        <StatCard
          label="Functions"
          value={formatInt(stats.counts.functions)}
          hint={`${formatInt(stats.counts.test_functions)} are tests`}
        />
        <StatCard label="Call edges" value={formatInt(stats.counts.call_edges)} hint={`${formatInt(stats.counts.import_edges)} import edges`} />
        <StatCard
          label="Exact resolution"
          tone="accent"
          value={formatPercent(stats.resolution.internal_exact_rate)}
          hint={`${formatPercent(stats.resolution.internal_resolution_rate)} resolved overall`}
        />
      </div>

      <p className="text-xs text-faint">
        {stats.summary} · parsed in {formatSeconds(stats.timings.parse_seconds)}, resolved in{" "}
        {formatSeconds(stats.timings.resolve_seconds)}, written in {formatSeconds(stats.timings.write_seconds)}
      </p>

      <Section title="How every call site was resolved" icon={<AlertTriangle size={16} />} subtitle={`${formatInt(stats.resolution.call_sites_total)} call sites`}>
        <ResolutionBar r={stats.resolution} />
      </Section>

      <Section
        title="Import cycles"
        icon={<RefreshCcw size={16} />}
        subtitle={cycles.data ? `${cycles.data.total} found` : undefined}
      >
        {cycles.isLoading && <Skeleton className="m-4 h-16" />}
        {cycles.error && <div className="p-4"><ErrorNote message={cycles.error.message} /></div>}
        {cycles.data && cycles.data.total === 0 && (
          <EmptyState title="No runtime import cycles">
            {cycles.data.note}
          </EmptyState>
        )}
        {cycles.data && cycles.data.total > 0 && (
          <>
            <ul className="divide-y divide-line/60">
              {cycles.data.cycles.map((cycle) => (
                <li key={cycle.modules.join(">")} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 py-3 font-mono text-xs">
                  {[...cycle.modules, cycle.modules[0]].map((m, i) => (
                    <span key={`${m}${i}`} className="flex items-center gap-2">
                      {i > 0 && <ArrowRight size={12} className="text-faint" />}
                      <span className={i === 0 || i === cycle.modules.length ? "text-accent" : "text-ink"}>{m}</span>
                    </span>
                  ))}
                </li>
              ))}
            </ul>
            <p className="border-t border-line px-4 py-2.5 text-2xs text-faint">{cycles.data.note}</p>
          </>
        )}
      </Section>

      <Section
        title="Hotspots"
        icon={<Flame size={16} />}
        subtitle={hotspots.data?.formula}
      >
        {hotspots.isLoading && <Skeleton className="m-4 h-40" />}
        {hotspots.error && <div className="p-4"><ErrorNote message={hotspots.error.message} /></div>}
        {hotspots.data && hotspots.data.hotspots.length === 0 && (
          <EmptyState title="No hotspots">No function has three or more high-confidence callers.</EmptyState>
        )}
        {hotspots.data && hotspots.data.hotspots.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Function</th>
                  <th className="th text-right">Callers</th>
                  <th className="th text-right">Complexity</th>
                  <th className="th w-48">Risk</th>
                </tr>
              </thead>
              <tbody>
                {(() => {
                  const max = Math.max(...hotspots.data.hotspots.map((h) => h.risk_score), 1);
                  return hotspots.data.hotspots.map((h) => (
                    <tr key={h.fqn} className="border-b border-line/60 transition-colors hover:bg-raised">
                      <td className="td max-w-0">
                        <Link to={explore(h.fqn)} className="block min-w-0">
                          <span className="block truncate font-mono text-xs font-semibold hover:text-accent">{displayName(h.fqn)}</span>
                          <span className="block truncate font-mono text-2xs text-faint">{splitFqn(h.fqn).scope}</span>
                        </Link>
                      </td>
                      <td className="td text-right font-mono text-xs">{h.fan_in}</td>
                      <td className="td text-right font-mono text-xs">{h.complexity}</td>
                      <td className="td">
                        <span className="flex items-center gap-2">
                          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
                            <span className="block h-full rounded-full bg-accent" style={{ width: `${(h.risk_score / max) * 100}%` }} />
                          </span>
                          <span className="w-10 text-right font-mono text-xs text-soft">{h.risk_score}</span>
                        </span>
                      </td>
                    </tr>
                  ));
                })()}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Dead-code candidates" icon={<Skull size={16} />} subtitle={dead.data ? `${dead.data.total} candidates` : undefined}>
        {dead.data && (
          <div className="flex items-start gap-2.5 border-b border-warn/25 bg-warn/10 px-4 py-2.5 text-xs leading-relaxed text-warn">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {dead.data.caveat}
          </div>
        )}
        {dead.isLoading && <Skeleton className="m-4 h-40" />}
        {dead.error && <div className="p-4"><ErrorNote message={dead.error.message} /></div>}
        {dead.data && dead.data.candidates.length === 0 && <EmptyState title="No candidates found" />}
        {dead.data && dead.data.candidates.length > 0 && (
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-line">
                  <th className="th">Function</th>
                  <th className="th">Module</th>
                  <th className="th text-right">Line</th>
                  <th className="th text-right">Complexity</th>
                </tr>
              </thead>
              <tbody>
                {dead.data.candidates.map((c) => (
                  <tr key={c.fqn} className="border-b border-line/60 hover:bg-raised">
                    <td className="td font-mono text-xs font-semibold">
                      <Link to={explore(c.fqn)} className="hover:text-accent">{displayName(c.fqn)}</Link>
                    </td>
                    <td className="td max-w-0 truncate font-mono text-2xs text-faint">{c.module}</td>
                    <td className="td text-right font-mono text-xs text-soft">{c.line ?? "–"}</td>
                    <td className="td text-right font-mono text-xs">{c.complexity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
