import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import {
  isTerminal,
  type Briefing,
  type CyclesResponse,
  type DeadCodeResponse,
  type GraphResponse,
  type Health,
  type HotspotsResponse,
  type ImpactResponse,
  type RepoDetail,
  type RepoSummary,
  type SymbolDetail,
  type SymbolHit,
  type TestSelectionResponse,
} from "./types";

/** Poll once a second while any repository is still being analysed. */
const POLL_MS = 1000;

export const useHealth = () =>
  useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<Health>("/health"),
    refetchInterval: 15_000,
    retry: false,
  });

export const useRepos = () =>
  useQuery({
    queryKey: ["repos"],
    queryFn: () => api.get<RepoSummary[]>("/repos"),
    refetchInterval: (query) =>
      query.state.data?.some((repo) => !isTerminal(repo.status)) ? POLL_MS : false,
  });

export const useRepo = (repoId: string | undefined) =>
  useQuery({
    queryKey: ["repo", repoId],
    enabled: !!repoId,
    queryFn: () => api.get<RepoDetail>(`/repos/${repoId}`),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !isTerminal(status) ? POLL_MS : false;
    },
  });

export const useCreateRepo = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { source: string; name?: string }) =>
      api.post<{ repo_id: string; status: string }>("/repos", body),
    onSuccess: () => client.invalidateQueries({ queryKey: ["repos"] }),
  });
};

export const useDeleteRepo = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (repoId: string) => api.delete(`/repos/${repoId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["repos"] }),
  });
};

export const useSymbols = (repoId: string, q: string) =>
  useQuery({
    queryKey: ["symbols", repoId, q],
    enabled: q.trim().length >= 2,
    placeholderData: keepPreviousData,
    queryFn: () => api.get<SymbolHit[]>(`/repos/${repoId}/symbols`, { q, limit: 40 }),
  });

export const useSymbol = (repoId: string, fqn: string | null) =>
  useQuery({
    queryKey: ["symbol", repoId, fqn],
    enabled: !!fqn,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    queryFn: () => api.get<SymbolDetail>(`/repos/${repoId}/symbols/${fqn}`),
  });

interface AnalysisArgs {
  repoId: string;
  fqn: string | null;
  depth?: number;
  minConfidence: number;
}

export const useImpact = ({ repoId, fqn, depth = 3, minConfidence }: AnalysisArgs) =>
  useQuery({
    queryKey: ["impact", repoId, fqn, depth, minConfidence],
    enabled: !!fqn,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.get<ImpactResponse>(`/repos/${repoId}/impact`, {
        fqn,
        depth,
        min_confidence: minConfidence,
        limit: 500,
      }),
  });

export const useTests = ({ repoId, fqn, minConfidence }: AnalysisArgs) =>
  useQuery({
    queryKey: ["tests", repoId, fqn, minConfidence],
    enabled: !!fqn,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.get<TestSelectionResponse>(`/repos/${repoId}/impact/tests`, {
        fqn,
        min_confidence: minConfidence,
      }),
  });

export const useBriefing = ({ repoId, fqn, minConfidence }: AnalysisArgs) =>
  useQuery({
    queryKey: ["briefing", repoId, fqn, minConfidence],
    enabled: !!fqn,
    placeholderData: keepPreviousData,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    queryFn: () =>
      api.get<Briefing>(`/repos/${repoId}/briefing`, { fqn, min_confidence: minConfidence }),
  });

export const useGraph = ({ repoId, fqn, depth = 2, minConfidence }: AnalysisArgs) =>
  useQuery({
    queryKey: ["graph", repoId, fqn, depth, minConfidence],
    enabled: !!fqn,
    placeholderData: keepPreviousData,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    queryFn: () =>
      api.get<GraphResponse>(`/repos/${repoId}/graph`, {
        fqn,
        depth,
        min_confidence: minConfidence,
        max_nodes: 90,
      }),
  });

export const useCycles = (repoId: string) =>
  useQuery({
    queryKey: ["cycles", repoId],
    queryFn: () => api.get<CyclesResponse>(`/repos/${repoId}/cycles`),
  });

export const useHotspots = (repoId: string, limit = 25) =>
  useQuery({
    queryKey: ["hotspots", repoId, limit],
    queryFn: () => api.get<HotspotsResponse>(`/repos/${repoId}/hotspots`, { limit }),
  });

export const useDeadCode = (repoId: string) =>
  useQuery({
    queryKey: ["dead-code", repoId],
    queryFn: () => api.get<DeadCodeResponse>(`/repos/${repoId}/dead-code`, { limit: 100 }),
  });
