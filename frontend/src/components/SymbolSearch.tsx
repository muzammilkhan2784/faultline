import clsx from "clsx";
import { Box, FileCode2, FunctionSquare, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSymbols } from "@/api/hooks";
import type { SymbolHit, SymbolKind } from "@/api/types";
import { displayName, splitFqn } from "@/lib/format";
import { Badge, EmptyState, ErrorNote, Skeleton } from "./ui";

interface Props {
  repoId: string;
  selected: string | null;
  onSelect: (fqn: string) => void;
  /** Shown in place of the "Find a function" hint whenever the box is not being used. */
  idle?: ReactNode;
}

const ORDER: SymbolKind[] = ["Function", "Class", "Module"];
const ICON: Record<SymbolKind, typeof FunctionSquare> = {
  Function: FunctionSquare,
  Class: Box,
  Module: FileCode2,
};

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function SymbolSearch({ repoId, selected, onSelect, idle }: Props) {
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const query = useDebounced(text, 250);
  const { data, isFetching, error } = useSymbols(repoId, query);
  const input = useRef<HTMLInputElement | null>(null);

  // `/` focuses the search box, like every good developer tool.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(target.tagName)) {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const grouped = useMemo(() => {
    const groups = new Map<SymbolKind, SymbolHit[]>();
    for (const hit of data ?? []) groups.set(hit.kind, [...(groups.get(hit.kind) ?? []), hit]);
    return ORDER.flatMap((kind) => (groups.has(kind) ? [[kind, groups.get(kind) ?? []] as const] : []));
  }, [data]);
  const flat = useMemo(() => grouped.flatMap(([, hits]) => hits), [grouped]);

  useEffect(() => setActive(0), [data]);

  // Only functions have a call graph. Choosing a class or module instead
  // narrows the search to its members, so it is always a useful click.
  const choose = (hit: SymbolHit) => {
    if (hit.kind === "Function") {
      // Clear the box so the rail goes back to describing the function just chosen.
      setText("");
      onSelect(hit.fqn);
    } else {
      setText(`${hit.fqn}.`);
      input.current?.focus();
    }
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, flat.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter" && flat[active]) {
      choose(flat[active]);
    } else if (event.key === "Escape") {
      setText("");
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="relative">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
        <input
          ref={input}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search functions…   ( / )"
          aria-label="Search symbols"
          spellCheck={false}
          className="input pl-9 pr-8"
        />
        {text && (
          <button
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-faint hover:text-ink"
            aria-label="Clear search"
            onClick={() => setText("")}
          >
            <X size={14} />
          </button>
        )}
      </div>

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1" role="listbox" aria-label="Search results">
        {error && <ErrorNote message={error.message} />}

        {text.trim().length < 2 &&
          (idle ?? (
            <EmptyState icon={<Search size={22} />} title="Find a function">
              Type two letters. Dotted names work: <code className="text-soft">billing.charge</code>
            </EmptyState>
          ))}

        {isFetching && !data && (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-11" />
            ))}
          </div>
        )}

        {query.trim().length >= 2 && data && flat.length === 0 && !isFetching && (
          <EmptyState title="No matches">Try a shorter name, or part of the module path.</EmptyState>
        )}

        {grouped.map(([kind, hits]) => {
          const Icon = ICON[kind];
          return (
            <section key={kind} className="mb-3">
              <h3 className="label mb-1 flex items-center gap-1.5 px-1">
                <Icon size={12} /> {kind}s
                <span className="font-mono normal-case tracking-normal text-faint">{hits.length}</span>
              </h3>
              <ul>
                {hits.map((hit) => {
                  const index = flat.indexOf(hit);
                  const { scope } = splitFqn(hit.fqn);
                  const name = hit.kind === "Function" ? displayName(hit.fqn) : hit.name;
                  return (
                    <li key={`${hit.kind}:${hit.fqn}`}>
                      <button
                        role="option"
                        aria-selected={hit.fqn === selected}
                        onMouseEnter={() => setActive(index)}
                        onClick={() => choose(hit)}
                        className={clsx(
                          "group flex w-full flex-col items-start rounded-lg border px-2.5 py-1.5 text-left transition-colors",
                          hit.fqn === selected
                            ? "border-accent/50 bg-accent/10"
                            : index === active
                              ? "border-line bg-raised"
                              : "border-transparent hover:bg-raised",
                        )}
                      >
                        <span className="flex w-full items-center gap-2">
                          <span className="truncate font-mono text-xs font-semibold text-ink">{name}</span>
                          {hit.is_test && <Badge tone="ok">test</Badge>}
                          {hit.kind !== "Function" && (
                            <span className="ml-auto text-2xs text-faint">browse members →</span>
                          )}
                        </span>
                        <span className="w-full truncate font-mono text-2xs text-faint">{scope}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
