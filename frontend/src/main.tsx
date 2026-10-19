import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "@/api/client";
import { AppShell } from "@/components/AppShell";
import { ReposPage } from "@/pages/ReposPage";
import "./index.css";

// The Explorer pulls in Cytoscape (~175 kB gzipped); the home screen does not need it.
const ExplorerPage = lazy(() => import("@/pages/ExplorerPage").then((m) => ({ default: m.ExplorerPage })));
const InsightsPage = lazy(() => import("@/pages/InsightsPage").then((m) => ({ default: m.InsightsPage })));

const Loading = () => (
  <div className="flex h-full items-center justify-center text-sm text-faint">Loading…</div>
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // A 4xx is an answer, not a glitch: retrying it just delays the message.
      retry: (count, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<ReposPage />} />
            <Route path="repos/:repoId" element={<Suspense fallback={<Loading />}><ExplorerPage /></Suspense>} />
            <Route path="repos/:repoId/insights" element={<Suspense fallback={<Loading />}><InsightsPage /></Suspense>} />
            <Route path="*" element={<ReposPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
