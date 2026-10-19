import clsx from "clsx";
import {
  Activity,
  AlertTriangle,
  Compass,
  FileSearch,
  FlaskConical,
  Info,
  MousePointerClick,
  Waves,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { useOutletContext, useParams, useSearchParams } from "react-router-dom";
import { useBriefing, useGraph, useHotspots, useImpact, useRepo, useSymbol, useTests } from "@/api/hooks";
import type { RepoOverview } from "@/api/types";
import { ApiError } from "@/api/client";
import { BriefingPanel, Prose } from "@/components/BriefingPanel";
import { ConfidenceFilter, DepthSlider } from "@/components/Controls";
import { GraphCanvas } from "@/components/GraphCanvas";
import { ImpactTable } from "@/components/ImpactTable";
import { InfoPanel } from "@/components/InfoPanel";
import { SymbolSearch } from "@/components/SymbolSearch";
import { TestSelectionPanel } from "@/components/TestSelectionPanel";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { displayName, formatInt, splitFqn } from "@/lib/format";

type Tab = "impact" | "tests" | "info";

function useExplorerParams() {
  const [params, setParams] = useSearchParams();
  const fqn = params.get("fqn");
  const depth = Math.min(5, Math.max(1, Number(params.get("depth") ?? 2) || 2));
  const conf = Math.min(1, Math.max(0, Number(params.get("conf") ?? 0.5)));
  const rawTab = params.get("tab");
  const tab: Tab = rawTab === "tests" || rawTab === "info" ? rawTab : "impact";

  // Choosing a symbol pushes a history entry so the browser's Back button works;
  // dragging a slider replaces, so it does not bury the history in noise.
  const update = useCallback(
    (patch: Record<string, string | null>, push: boolean) => {
      const next = new URLSearchParams(params);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      setParams(next, { replace: !push });
    },
    [params, setParams],
  );

  return {
    fqn,
    depth,
    conf,
    tab,
    setTab: (value: Tab) => update({ tab: value === "impact" ? null : value }, false),
    select: (next: string) => update({ fqn: next }, true),
    setDepth: (value: number) => update({ depth: String(value) }, false),
    setConf: (value: number) => update({ conf: value.toFixed(2) }, false),
  };
}

const STEPS = [
  {
    title: "Pick a function",
    body: "Search on the left, or start from one of the risky ones below.",
  },
  {
    title: "Read the summary",
    body: "The left panel explains in plain English what it does, what a change would affect, which tests to run and where the risk is.",
  },
  {
    title: "Explore the graph",
    body: "Circles are functions and arrows point at what they call. Ripples show how far a change would travel.",
  },
];

function Suggestions({
  repoId,
  name,
  overview,
  onSelect,
}: {
  repoId: string;
  name: string;
  overview: RepoOverview | null;
  onSelect: (fqn: string) => void;
}) {
  const { data, isLoading } = useHotspots(repoId, 6);
  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center gap-6 px-6 py-8">
      <div className="text-center">
        <Waves size={34} className="mx-auto text-accent" />
        <h2 className="mt-4 text-xl font-semibold tracking-tight">Pick a function to see what breaks</h2>
        <p className="mt-2 text-sm text-soft">
          Faultline has mapped <span className="font-mono text-ink">{name}</span>. Every function is a circle; an
          arrow means one function calls another.
        </p>
      </div>

      {overview && (
        <div className="rounded-xl border border-line bg-raised/40 p-4">
          <h3 className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
            <FileSearch size={15} className="text-accent" /> What Faultline found
          </h3>
          <Prose text={overview.text} className="text-[13px] leading-relaxed text-soft" />
          {overview.notices.length > 0 && (
            <ul className="mt-3 space-y-2">
              {overview.notices.map((notice) => (
                <li
                  key={notice.text}
                  className={clsx(
                    "flex gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed",
                    notice.level === "warn"
                      ? "border-warn/30 bg-warn/10 text-warn"
                      : "border-info/30 bg-info/10 text-info",
                  )}
                >
                  {notice.level === "warn" ? (
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  ) : (
                    <Info size={14} className="mt-0.5 shrink-0" />
                  )}
                  <Prose text={notice.text} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <ol className="grid gap-3 sm:grid-cols-3">
        {STEPS.map((step, index) => (
          <li key={step.title} className="rounded-xl border border-line p-3">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent/15 font-mono text-2xs text-accent">
                {index + 1}
              </span>
              {step.title}
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-soft">{step.body}</p>
          </li>
        ))}
      </ol>

      <div>
        <div className="label mb-1 flex items-center gap-1.5">
          <MousePointerClick size={12} /> Start with the riskiest code
        </div>
        <p className="mb-2 text-xs text-faint">
          Many callers and complicated internals: the most likely places for a change to go wrong.
        </p>
        <div className="space-y-2 text-left">
          {isLoading && [0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}
          {data?.hotspots.map((h) => (
            <button
              key={h.fqn}
              onClick={() => onSelect(h.fqn)}
              className="card flex w-full items-center justify-between gap-4 px-4 py-3 text-left transition-colors hover:border-accent/50"
            >
              <span className="min-w-0">
                <span className="block truncate font-mono text-sm font-semibold">{displayName(h.fqn)}</span>
                <span className="block truncate font-mono text-2xs text-faint">{splitFqn(h.fqn).scope}</span>
              </span>
              <span className="shrink-0 text-right">
                <span className="block font-mono text-sm font-semibold text-accent">{h.fan_in} callers</span>
                <span className="block text-2xs text-faint">complexity {h.complexity}</span>
              </span>
            </button>
          ))}
          {data && data.hotspots.length === 0 && (
            <p className="rounded-lg border border-line px-3 py-3 text-center text-xs text-faint">
              No function here is called by another one, so there is nothing risky to rank. Use the search box to
              look at any function on its own.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export function ExplorerPage() {
  const { repoId = "" } = useParams();
  const { theme } = useOutletContext<{ theme: string }>();
  const { fqn, depth, conf, tab, setTab, select, setDepth, setConf } = useExplorerParams();
  const [showImpact, setShowImpact] = useState(true);

  const repo = useRepo(repoId);
  const symbol = useSymbol(repoId, fqn);
  const graph = useGraph({ repoId, fqn, depth, minConfidence: conf });
  const impact = useImpact({ repoId, fqn, depth: 3, minConfidence: conf });
  const tests = useTests({ repoId, fqn, minConfidence: conf });
  const briefing = useBriefing({ repoId, fqn, minConfidence: conf });

  const distances = useMemo(
    () => (impact.data ? new Map(impact.data.results.map((r) => [r.fqn, r.distance])) : null),
    [impact.data],
  );

  if (repo.isLoading) return <div className="p-6"><Skeleton className="h-full min-h-[300px]" /></div>;
  if (repo.error) return <div className="p-6"><ErrorNote message={repo.error.message} /></div>;
  if (repo.data && repo.data.status !== "ready") {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState icon={<Activity size={26} className="animate-pulse" />} title={`This repository is ${repo.data.status}`}>
          {repo.data.status === "failed" ? repo.data.error : "The graph is being built. This page updates on its own."}
        </EmptyState>
      </div>
    );
  }

  const notFound = symbol.error instanceof ApiError && symbol.error.status === 404 ? symbol.error : null;
  const tabs: { id: Tab; label: string; icon: typeof Info; count?: number }[] = [
    { id: "impact", label: "Impact", icon: Waves, count: impact.data?.total },
    { id: "tests", label: "Tests", icon: FlaskConical, count: tests.data?.total },
    { id: "info", label: "Info", icon: Info },
  ];

  return (
    <div className="grid h-full grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[340px_minmax(0,1fr)_390px] lg:overflow-hidden">
      {/* left: search, and the plain-English briefing whenever you are not searching */}
      <aside className="card flex min-h-[280px] flex-col p-3 lg:min-h-0">
        <SymbolSearch
          repoId={repoId}
          selected={fqn}
          onSelect={select}
          idle={
            fqn && !notFound ? (
              <BriefingPanel
                data={briefing.data}
                loading={briefing.isFetching}
                error={briefing.error}
                onSelect={select}
                testCommand={tests.data && tests.data.total > 0 ? tests.data.command : undefined}
              />
            ) : undefined
          }
        />
      </aside>

      {/* centre: graph */}
      <section className="flex min-h-[460px] min-w-0 flex-col gap-3 lg:min-h-0">
        {fqn && !notFound && (
          <div className="card flex flex-wrap items-end gap-x-6 gap-y-3 px-4 py-3">
            <div className="min-w-0 flex-1 basis-56">
              <div className="label">Selected</div>
              <div className="truncate font-mono text-sm font-semibold" title={fqn}>
                {displayName(fqn)}
              </div>
              <div className="truncate font-mono text-2xs text-faint">{fqn}</div>
            </div>
            <DepthSlider value={depth} onChange={setDepth} />
            <ConfidenceFilter value={conf} onChange={setConf} />
            <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-soft">
              <input
                type="checkbox"
                checked={showImpact}
                onChange={(event) => setShowImpact(event.target.checked)}
                className="h-4 w-4 accent-[rgb(var(--accent))]"
              />
              Shockwave
            </label>
          </div>
        )}

        <div className="relative min-h-0 flex-1">
          {!fqn && (
            <div className="card h-full overflow-y-auto">
              <Suggestions
                repoId={repoId}
                name={repo.data?.name ?? "this repository"}
                overview={repo.data?.overview ?? null}
                onSelect={select}
              />
            </div>
          )}
          {notFound && (
            <div className="card h-full">
              <EmptyState icon={<Compass size={26} />} title="That symbol does not exist in this repository">
                {notFound.didYouMean.length > 0 ? (
                  <span>
                    Did you mean:
                    <span className="mt-2 flex flex-col items-center gap-1">
                      {notFound.didYouMean.map((candidate) => (
                        <button key={candidate} className="font-mono text-accent hover:underline" onClick={() => select(candidate)}>
                          {candidate}
                        </button>
                      ))}
                    </span>
                  </span>
                ) : (
                  "Use the search box to find one."
                )}
              </EmptyState>
            </div>
          )}
          {fqn && !notFound && (
            <GraphCanvas
              data={graph.data}
              selectedId={fqn}
              distances={distances}
              showImpact={showImpact}
              onSelect={select}
              themeKey={theme}
              loading={graph.isFetching}
            />
          )}
          {fqn && graph.error && !notFound && (
            <div className="absolute inset-x-4 top-4 z-30"><ErrorNote message={graph.error.message} /></div>
          )}
        </div>
      </section>

      {/* right: details */}
      <aside className="card flex min-h-[360px] flex-col lg:min-h-0">
        {!fqn || notFound ? (
          <EmptyState icon={<Waves size={22} />} title="Impact appears here">
            Pick a function to see everything that would be affected, the tests to run, and its details.
          </EmptyState>
        ) : (
          <>
            <div className="border-b border-line p-4">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <div className="font-mono text-3xl font-semibold tracking-tight text-accent">
                    {impact.data ? formatInt(impact.data.total) : "–"}
                  </div>
                  <div className="text-xs text-soft">
                    function{impact.data?.total === 1 ? "" : "s"} can reach this within 3 calls
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-xl font-semibold text-ok">
                    {impact.data ? formatInt(impact.data.affected_tests) : "–"}
                  </div>
                  <div className="text-xs text-soft">tests among them</div>
                </div>
              </div>
            </div>

            <div role="tablist" aria-label="Details" className="flex border-b border-line px-2">
              {tabs.map(({ id, label, icon: Icon, count }) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={clsx(
                    "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                    tab === id ? "border-accent text-ink" : "border-transparent text-soft hover:text-ink",
                  )}
                >
                  <Icon size={14} />
                  {label}
                  {count !== undefined && (
                    <span className="rounded-full bg-raised px-1.5 font-mono text-2xs text-soft">{count}</span>
                  )}
                </button>
              ))}
            </div>

            <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto p-3">
              {tab === "impact" && (
                impact.data ? (
                  <ImpactTable rows={impact.data.results} selectedId={fqn} onSelect={select} />
                ) : impact.error ? (
                  <ErrorNote message={impact.error.message} />
                ) : (
                  <div className="space-y-2">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-11" />)}</div>
                )
              )}
              {tab === "tests" && (
                tests.data ? (
                  <TestSelectionPanel data={tests.data} onSelect={select} />
                ) : tests.error ? (
                  <ErrorNote message={tests.error.message} />
                ) : (
                  <Skeleton className="h-40" />
                )
              )}
              {tab === "info" && (
                symbol.data ? (
                  <InfoPanel symbol={symbol.data} />
                ) : symbol.error ? (
                  <ErrorNote message={symbol.error.message} />
                ) : (
                  <Skeleton className="h-52" />
                )
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
