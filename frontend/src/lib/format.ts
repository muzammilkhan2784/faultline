const int = new Intl.NumberFormat("en-US");

export const formatInt = (n: number | null | undefined): string =>
  n === null || n === undefined ? "–" : int.format(n);

export const formatPercent = (ratio: number | null | undefined, digits = 1): string =>
  ratio === null || ratio === undefined ? "–" : `${(ratio * 100).toFixed(digits)}%`;

export const formatSeconds = (s: number | null | undefined): string => {
  if (s === null || s === undefined) return "–";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
};

export const formatConfidence = (c: number): string => c.toFixed(2).replace(/0$/, "");

/** `pkg.mod.Class.method` → `{ scope: "pkg.mod.Class", name: "method" }`. */
export function splitFqn(fqn: string): { scope: string; name: string } {
  const cut = fqn.lastIndexOf(".");
  if (cut < 0) return { scope: "", name: fqn };
  // Nested functions carry a `<locals>` marker; keep it with the scope.
  return { scope: fqn.slice(0, cut), name: fqn.slice(cut + 1) };
}

/**
 * The name to show for a function: `URL.__init__` for a method, `urlparse` for a
 * plain function. A bare `__init__` says nothing when a codebase has 200 of them.
 */
export function displayName(fqn: string): string {
  const parts = fqn.split(".").filter((p) => p !== "<locals>");
  const name = parts[parts.length - 1] ?? fqn;
  const parent = parts[parts.length - 2];
  return parent && /^[A-Z]/.test(parent) ? `${parent}.${name}` : name;
}

/** A short, human-friendly label: the last two dotted segments. */
export function shortFqn(fqn: string): string {
  const parts = fqn.split(".").filter((p) => p !== "<locals>");
  return parts.slice(-2).join(".");
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  const units: [number, string][] = [
    [60, "s"],
    [3600, "m"],
    [86400, "h"],
    [Infinity, "d"],
  ];
  let divisor = 1;
  for (const [limit, label] of units) {
    if (seconds < limit) return `${Math.max(1, Math.floor(seconds / divisor))}${label} ago`;
    divisor = limit;
  }
  return "";
}

export const confidenceTone = (c: number): "ok" | "warn" | "bad" =>
  c >= 0.9 ? "ok" : c >= 0.6 ? "warn" : "bad";
