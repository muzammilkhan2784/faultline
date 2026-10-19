import clsx from "clsx";
import { ArrowDown, ArrowUp, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import type { Reach } from "@/api/types";
import { displayName, splitFqn } from "@/lib/format";
import { Badge, ConfidenceBar, EmptyState } from "./ui";

type SortKey = "distance" | "name" | "path_count" | "confidence";

interface Props {
  rows: Reach[];
  selectedId: string | null;
  onSelect: (fqn: string) => void;
}

const WAVE_DOT = ["", "bg-wave1", "bg-wave2", "bg-wave3"];

export function ImpactTable({ rows, selectedId, onSelect }: Props) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "distance", dir: 1 });

  const sorted = useMemo(() => {
    const value = (r: Reach): number | string =>
      sort.key === "name" ? r.name.toLowerCase() : r[sort.key];
    return [...rows].sort((a, b) => {
      const [x, y] = [value(a), value(b)];
      const primary = x < y ? -1 : x > y ? 1 : 0;
      // A stable, sensible tie-break: nearest first, then most certain.
      return primary * sort.dir || a.distance - b.distance || b.confidence - a.confidence;
    });
  }, [rows, sort]);

  if (rows.length === 0) {
    return (
      <EmptyState icon={<ShieldAlert size={22} />} title="Nothing reaches this function">
        No function in this repository calls it — directly or through up to the chosen depth — at this
        confidence. It may be an entrypoint, or unused.
      </EmptyState>
    );
  }

  const header = (key: SortKey, label: string, align: "left" | "right" = "left") => {
    const isActive = sort.key === key;
    return (
      <th
        className={clsx("th", align === "right" && "text-right")}
        aria-sort={isActive ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
      >
        <button
          className={clsx("inline-flex items-center gap-1 uppercase tracking-wider hover:text-ink", isActive && "text-ink")}
          onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === "confidence" || key === "path_count" ? -1 : 1 }))}
        >
          {label}
          {isActive && (sort.dir === 1 ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
        </button>
      </th>
    );
  };

  return (
    <table className="w-full table-fixed border-separate border-spacing-0 text-left">
      <colgroup>
        <col className="w-14" />
        <col />
        <col className="w-12" />
        <col className="w-[6.75rem]" />
      </colgroup>
      <thead className="sticky top-0 z-10 bg-surface">
        <tr className="border-b border-line">
          {header("distance", "Hops")}
          {header("name", "Function")}
          {header("path_count", "Paths", "right")}
          {header("confidence", "Conf.")}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => {
          const { scope } = splitFqn(r.fqn);
          return (
            <tr
              key={r.fqn}
              onClick={() => onSelect(r.fqn)}
              className={clsx(
                "cursor-pointer border-b border-line/60 transition-colors hover:bg-raised",
                r.fqn === selectedId && "bg-accent/10",
              )}
            >
              <td className="td w-12">
                <span className="inline-flex items-center gap-1.5 font-mono text-xs">
                  <span className={clsx("h-2 w-2 rounded-full", WAVE_DOT[Math.min(r.distance, 3)])} />
                  {r.distance}
                </span>
              </td>
              <td className="td max-w-0">
                <div className="line-clamp-2 break-all font-mono text-xs font-semibold text-ink" title={r.fqn}>
                  {displayName(r.fqn)}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  {r.is_test && <Badge tone="ok">test</Badge>}
                  {r.is_entrypoint && <Badge tone="info">entry</Badge>}
                  <span className="min-w-0 truncate font-mono text-2xs text-faint">{scope}</span>
                </div>
              </td>
              <td className="td text-right font-mono text-xs text-soft">{r.path_count}</td>
              <td className="td">
                <ConfidenceBar value={r.confidence} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
