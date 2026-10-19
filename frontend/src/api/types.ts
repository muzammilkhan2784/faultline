/** Mirrors backend/faultline/api/schemas.py. Keep the two in step. */

export type RepoStatus = "pending" | "parsing" | "writing" | "ready" | "failed";
export type SymbolKind = "Function" | "Class" | "Module";
export type Resolution = "exact" | "inferred" | "ambiguous";

export const isTerminal = (status: RepoStatus): boolean =>
  status === "ready" || status === "failed";

export interface RepoSummary {
  id: string;
  name: string;
  source: string;
  status: RepoStatus;
  commit_sha: string | null;
  analyzed_at: string | null;
  file_count: number | null;
  loc: number | null;
  node_count: number | null;
  edge_count: number | null;
  total_seconds: number | null;
  error: string | null;
  /** A one-line warning when the analysis found little to show. */
  notice: string | null;
}

export interface ResolutionBreakdown {
  call_sites_total: number;
  resolved_exact: number;
  resolved_inferred: number;
  resolved_ambiguous: number;
  dropped_builtin: number;
  dropped_external: number;
  dropped_too_ambiguous: number;
  unresolved: number;
  internal_call_sites: number;
  exact_rate: number;
  resolution_rate: number;
  internal_exact_rate: number;
  internal_resolution_rate: number;
}

export interface RepoStats {
  repo_id: string;
  counts: {
    modules: number;
    classes: number;
    functions: number;
    test_functions: number;
    call_edges: number;
    import_edges: number;
  };
  timings: {
    parse_seconds: number | null;
    resolve_seconds: number | null;
    write_seconds: number | null;
    total_seconds: number | null;
  };
  resolution: ResolutionBreakdown;
  parse_failures: { fqn: string; path: string; error: string }[];
  summary: string;
}

export interface RepoNotice {
  level: "info" | "warn";
  text: string;
}

export interface RepoOverview {
  text: string;
  notices: RepoNotice[];
}

export interface RepoDetail extends RepoSummary {
  stats: RepoStats | null;
  overview: RepoOverview | null;
}

export interface SymbolHit {
  fqn: string;
  name: string;
  kind: SymbolKind;
  module: string;
  line: number;
  is_test: boolean;
  score: number;
}

export interface SymbolDetail {
  fqn: string;
  name: string;
  module: string;
  class_fqn: string | null;
  line_start: number;
  line_end: number;
  is_method: boolean;
  is_async: boolean;
  is_test: boolean;
  is_entrypoint: boolean;
  is_private: boolean;
  param_count: number;
  complexity: number;
  decorators: string[];
  doc: string | null;
  fan_in: number;
  fan_out: number;
}

export interface Reach {
  fqn: string;
  name: string;
  module: string;
  class_fqn: string | null;
  is_test: boolean;
  is_entrypoint: boolean;
  line: number | null;
  complexity: number | null;
  distance: number;
  path_count: number;
  confidence: number;
}

export interface ImpactResponse {
  fqn: string;
  depth: number;
  min_confidence: number;
  total: number;
  affected_tests: number;
  results: Reach[];
}

export interface SelectedTest {
  fqn: string;
  name: string;
  module: string;
  path: string | null;
  class_fqn: string | null;
  distance: number;
  confidence: number;
}

export interface TestSelectionResponse {
  fqn: string;
  total: number;
  tests: SelectedTest[];
  command: string;
}

export type RiskLevel = "low" | "moderate" | "high";

export interface BriefingLink {
  fqn: string;
  label: string;
  note: string;
}

export interface BriefingSection {
  id: "what" | "affected" | "tests" | "risk";
  title: string;
  /** Prose; names are wrapped in backticks. */
  text: string;
  links: BriefingLink[];
}

export interface Briefing {
  fqn: string;
  depth: number;
  min_confidence: number;
  headline: string;
  risk_level: RiskLevel;
  sections: BriefingSection[];
}

export interface GraphNode {
  id: string;
  name: string;
  module: string | null;
  class_fqn: string | null;
  is_test: boolean;
  is_entrypoint: boolean;
  complexity: number | null;
  line: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  confidence: number;
  line: number | null;
  resolution: Resolution | null;
  via: string | null;
}

export interface GraphResponse {
  center: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  total: number;
  truncated: boolean;
}

export interface CyclesResponse {
  total: number;
  cycles: { modules: string[]; size: number; is_self_import: boolean }[];
  note: string;
}

export interface Hotspot {
  fqn: string;
  name: string;
  module: string;
  line: number | null;
  fan_in: number;
  complexity: number;
  risk_score: number;
}

export interface HotspotsResponse {
  total: number;
  formula: string;
  hotspots: Hotspot[];
}

export interface DeadCodeResponse {
  total: number;
  caveat: string;
  candidates: {
    fqn: string;
    name: string;
    module: string;
    line: number | null;
    complexity: number;
    is_private: boolean;
  }[];
}

export interface Health {
  status: "ok" | "degraded";
  neo4j: boolean;
  version: string;
}
