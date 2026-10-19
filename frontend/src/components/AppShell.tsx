import clsx from "clsx";
import { ChevronRight, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useMatch } from "react-router-dom";
import { useHealth, useRepo } from "@/api/hooks";
import { Logo } from "./ui";

export const useTheme = () => {
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (document.documentElement.dataset.theme as "dark" | "light") ?? "dark",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("faultline-theme", theme);
    } catch {
      /* private mode: the theme just will not persist */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))] as const;
};

function RepoCrumb({ repoId }: { repoId: string }) {
  const { data } = useRepo(repoId);
  return (
    <>
      <ChevronRight size={14} className="text-faint" />
      <span className="max-w-[16rem] truncate font-medium text-ink">{data?.name ?? "…"}</span>
    </>
  );
}

const tab = ({ isActive }: { isActive: boolean }) =>
  clsx(
    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
    isActive ? "bg-raised text-ink" : "text-soft hover:text-ink",
  );

export function AppShell() {
  const [theme, toggleTheme] = useTheme();
  const health = useHealth();
  const match = useMatch("/repos/:repoId/*");
  const repoId = match?.params.repoId;

  const dbState = health.isError
    ? { tone: "bg-bad", text: "API unreachable" }
    : health.data?.neo4j === false
      ? { tone: "bg-warn", text: "Database down" }
      : health.data
        ? { tone: "bg-ok", text: "Connected" }
        : { tone: "bg-faint", text: "Checking…" };

  return (
    <div className="flex h-full flex-col">
      <header className="z-40 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-bg/80 px-4 backdrop-blur">
        <Link to="/" className="flex items-center gap-2.5" aria-label="Faultline home">
          <Logo />
          <span className="text-[15px] font-semibold tracking-tight">Faultline</span>
        </Link>

        <nav className="ml-2 flex items-center gap-1.5 text-sm" aria-label="Breadcrumb">
          {repoId && <RepoCrumb repoId={repoId} />}
        </nav>

        {repoId && (
          <nav className="ml-4 flex items-center gap-1" aria-label="Sections">
            <NavLink to={`/repos/${repoId}`} end className={tab}>
              Explorer
            </NavLink>
            <NavLink to={`/repos/${repoId}/insights`} className={tab}>
              Insights
            </NavLink>
          </nav>
        )}

        <div className="ml-auto flex items-center gap-3">
          <span className="hidden items-center gap-2 text-xs text-faint sm:flex" title={health.data ? `API v${health.data.version}` : ""}>
            <span className={clsx("h-2 w-2 rounded-full", dbState.tone)} />
            {dbState.text}
          </span>
          <button
            className="btn px-2"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title="Toggle theme"
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet context={{ theme }} />
      </main>
    </div>
  );
}
