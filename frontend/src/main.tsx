import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "@xyflow/react/dist/style.css";
import "./index.css";

type ErrorBoundaryState = { hasError: boolean };

/**
 * Keep a transient attachment/provider failure from taking down the entire
 * desktop shell. React otherwise replaces the root with a blank page when a
 * render-time exception escapes a child component. The retry is intentionally
 * a full reload so stale module state and an expired bridge token are cleared.
 */
class AppErrorBoundary extends React.Component<React.PropsWithChildren, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    // Keep diagnostics local; do not send prompts, files, or credentials to a
    // third party. Sentry, when configured, can capture this through the
    // browser's normal error integration.
    console.error("Smara UI recovered from an unexpected error", error);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950 text-gray-100 px-6">
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-white/[.04] p-6 text-center shadow-2xl">
          <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-indigo-500/20 text-indigo-200">✦</div>
          <h1 className="text-lg font-semibold">Smara needs a quick refresh</h1>
          <p className="mt-2 text-sm text-gray-400">The last action did not finish cleanly. Your conversation is safe.</p>
          <button
            type="button"
            className="mt-5 rounded-xl bg-indigo-400 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-indigo-300"
            onClick={() => window.location.reload()}
          >
            Refresh Smara
          </button>
        </div>
      </div>
    );
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </AppErrorBoundary>
  </React.StrictMode>
);
