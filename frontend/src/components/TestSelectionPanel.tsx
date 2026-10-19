import { FlaskConical, Terminal } from "lucide-react";
import { useMemo } from "react";
import type { TestSelectionResponse } from "@/api/types";
import { CopyButton, EmptyState } from "./ui";

interface Props {
  data: TestSelectionResponse;
  onSelect: (fqn: string) => void;
}

export function TestSelectionPanel({ data, onSelect }: Props) {
  const byFile = useMemo(() => {
    const groups = new Map<string, TestSelectionResponse["tests"]>();
    for (const test of data.tests) {
      const key = test.path ?? test.module;
      groups.set(key, [...(groups.get(key) ?? []), test]);
    }
    return [...groups.entries()];
  }, [data.tests]);

  if (data.total === 0) {
    return (
      <EmptyState icon={<FlaskConical size={22} />} title="No test reaches this function">
        Nothing in the test suite calls it, even transitively, at this confidence. Either it is untested, or
        it is reached in a way static analysis cannot see (fixtures, reflection).
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="label flex items-center gap-1.5">
            <Terminal size={12} /> Run only these
          </span>
          <CopyButton text={data.command} label="Copy command" />
        </div>
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-bg p-3 text-xs leading-relaxed text-ink">
          <span className="select-none text-faint">$ </span>
          {data.command}
        </pre>
        <p className="mt-1.5 text-2xs text-faint">
          {data.total} test{data.total === 1 ? "" : "s"} reach this function. Running just these is the
          fast path; the full suite is the safe one.
        </p>
      </div>

      {byFile.map(([file, tests]) => (
        <section key={file}>
          <h3 className="mb-1 truncate font-mono text-2xs font-semibold text-soft">{file}</h3>
          <ul className="space-y-0.5">
            {tests.map((test) => (
              <li key={test.fqn}>
                <button
                  onClick={() => onSelect(test.fqn)}
                  className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left hover:bg-raised"
                >
                  <span className="truncate font-mono text-xs text-ink">{test.name}</span>
                  <span className="shrink-0 font-mono text-2xs text-faint">
                    {test.distance} hop{test.distance === 1 ? "" : "s"} · {test.confidence.toFixed(2)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
