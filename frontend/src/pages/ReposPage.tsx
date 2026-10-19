import { AlertTriangle, ArrowRight, GitBranch, Loader2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateRepo, useDeleteRepo, useRepos } from "@/api/hooks";
import type { RepoSummary } from "@/api/types";
import { EmptyState, ErrorNote, Skeleton, StatusPill } from "@/components/ui";
import { formatInt, formatSeconds, relativeTime } from "@/lib/format";

const EXAMPLES = [
  { label: "pallets/flask", url: "https://github.com/pallets/flask" },
  { label: "psf/requests", url: "https://github.com/psf/requests" },
  { label: "encode/httpx", url: "https://github.com/encode/httpx" },
];

function AddRepo() {
  const [source, setSource] = useState("");
  const create = useCreateRepo();
  const navigate = useNavigate();

  const submit = (value: string) => {
    if (!value.trim()) return;
    create.mutate(
      { source: value.trim() },
      {
        onSuccess: () => setSource(""),
      },
    );
  };

  return (
    <div className="card p-5">
      <h2 className="text-base font-semibold">Analyse a repository</h2>
      <p className="mt-1 text-sm text-soft">
        Paste a public GitHub URL, or the path to a project folder. Faultline maps every function, class and
        call into a graph you can query. It reads Python (<code className="font-mono text-ink">.py</code>) files
        only.
      </p>
      <details className="group mt-2 text-xs text-faint">
        <summary className="cursor-pointer select-none text-soft hover:text-ink">
          Want to analyse code from your own computer?
        </summary>
        <ul className="mt-1.5 max-w-2xl list-disc space-y-1 pl-4 leading-relaxed">
          <li>
            <span className="text-soft">Easiest:</span> push it to GitHub and paste the URL above (public repos).
          </li>
          <li>
            <span className="text-soft">With Docker:</span> the server can&apos;t see your drives, so copy the
            project into the <code className="font-mono text-ink">.repos</code> folder next to{" "}
            <code className="font-mono text-ink">docker-compose.yml</code> and enter{" "}
            <code className="font-mono text-ink">/repos/your-folder-name</code>.
          </li>
          <li>
            <span className="text-soft">Without the web app:</span> run{" "}
            <code className="font-mono text-ink">faultline analyze path/to/project</code> in a terminal. It needs
            no Docker and no database.
          </li>
        </ul>
      </details>
      <form
        className="mt-4 flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          submit(source);
        }}
      >
        <input
          className="input font-mono"
          placeholder="https://github.com/pallets/flask"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          aria-label="Repository URL or path"
          spellCheck={false}
        />
        <button className="btn btn-accent shrink-0" disabled={create.isPending || !source.trim()}>
          {create.isPending ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
          Analyse
        </button>
      </form>
      {create.isError && (
        <div className="mt-3">
          <ErrorNote message={create.error.message} />
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-faint">
        Try:
        {EXAMPLES.map((example) => (
          <button
            key={example.url}
            className="rounded-md border border-line bg-raised px-2 py-1 font-mono text-soft transition-colors hover:border-accent/50 hover:text-ink"
            onClick={() => {
              setSource(example.url);
              submit(example.url);
              navigate("/");
            }}
          >
            {example.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function RepoCard({ repo }: { repo: RepoSummary }) {
  const remove = useDeleteRepo();
  const ready = repo.status === "ready";
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <GitBranch size={15} className="shrink-0 text-faint" />
            <h3 className="truncate text-[15px] font-semibold">{repo.name}</h3>
          </div>
          <p className="mt-0.5 truncate font-mono text-2xs text-faint" title={repo.source}>
            {repo.source}
          </p>
        </div>
        <StatusPill status={repo.status} />
      </div>

      {repo.status === "failed" && repo.error && (
        <p className="mt-3 rounded-md bg-bad/10 px-2.5 py-1.5 text-xs leading-relaxed text-bad">{repo.error}</p>
      )}
      {ready && repo.notice && (
        <p className="mt-3 flex gap-1.5 rounded-md bg-warn/10 px-2.5 py-1.5 text-xs leading-relaxed text-warn">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          {repo.notice}
        </p>
      )}

      <dl className="mt-4 grid grid-cols-4 gap-2">
        {[
          ["Files", formatInt(repo.file_count)],
          ["Lines", formatInt(repo.loc)],
          ["Nodes", formatInt(repo.node_count)],
          ["Edges", formatInt(repo.edge_count)],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="label">{label}</dt>
            <dd className="font-mono text-sm font-semibold">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-4 flex items-center justify-between text-xs text-faint">
        <span>
          {ready
            ? `Analysed in ${formatSeconds(repo.total_seconds)} · ${relativeTime(repo.analyzed_at)}`
            : repo.status === "failed"
              ? "Analysis failed"
              : "Working…"}
        </span>
        {ready && (
          <span className="flex items-center gap-1 font-medium text-accent">
            Explore <ArrowRight size={13} />
          </span>
        )}
      </div>
    </>
  );

  return (
    <li className="group relative animate-rise">
      {ready ? (
        <Link to={`/repos/${repo.id}`} className="card block p-4 transition-colors hover:border-accent/50">
          {body}
        </Link>
      ) : (
        <div className="card p-4">{body}</div>
      )}
      <button
        className="btn btn-ghost absolute bottom-3 right-3 p-1.5 opacity-0 transition-opacity focus:opacity-100 group-hover:opacity-100"
        aria-label={`Delete ${repo.name}`}
        title="Delete"
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(`Delete ${repo.name} and its graph?`)) remove.mutate(repo.id);
        }}
      >
        <Trash2 size={14} />
      </button>
    </li>
  );
}

export function ReposPage() {
  const { data, isLoading, error } = useRepos();

  return (
    <div className="mx-auto h-full max-w-5xl overflow-y-auto px-5 py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">
          What breaks when you change <span className="text-accent">this</span>?
        </h1>
        <p className="mt-2 max-w-2xl text-soft">
          Faultline turns a Python codebase into a call graph, then answers three questions: what is affected,
          which tests to run, and where the risk is concentrated.
        </p>
      </div>

      <AddRepo />

      <h2 className="mb-3 mt-10 text-sm font-semibold text-soft">Repositories</h2>
      {error && <ErrorNote message={error.message} />}
      {isLoading && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      )}
      {data && data.length === 0 && (
        <div className="card">
          <EmptyState icon={<GitBranch size={26} />} title="Nothing analysed yet">
            Pick one of the examples above. A project like Flask takes about two seconds.
          </EmptyState>
        </div>
      )}
      {data && data.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2">
          {data.map((repo) => (
            <RepoCard key={repo.id} repo={repo} />
          ))}
        </ul>
      )}
    </div>
  );
}
