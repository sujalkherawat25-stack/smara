import { useState } from "react";
import { apiClient } from "@/api/client";
import { useChatStore } from "@/stores/chatStore";
import type { BenchmarkResult } from "@/types/memory";

export default function BenchmarkModal() {
  const conversationId = useChatStore((s) => s.conversationId);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<BenchmarkResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [votes, setVotes] = useState<{ mem0g: number; fullContext: number; total: number }>({
    mem0g: 0,
    fullContext: 0,
    total: 0,
  });

  const runBenchmark = async () => {
    if (!query.trim() || loading) return;
    setLoading(true);
    try {
      const res = await apiClient.post<BenchmarkResult>("/v1/benchmark", {
        message: query,
        conversation_id: conversationId,
      });
      setResult(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const vote = async (winner: "mem0g" | "fullContext") => {
    if (!result) return;
    try {
      const res = await apiClient.post<{
        summary: { mem0g_wins: number; full_context_wins: number; total_runs: number };
      }>("/v1/benchmark/vote", {
        benchmark_id: result.id,
        winner: winner === "mem0g" ? "mem0g" : "full_context",
      });
      const s = res.summary;
      setVotes({
        mem0g: s.mem0g_wins,
        fullContext: s.full_context_wins,
        total: s.total_runs,
      });
    } catch {}
  };

  return (
    <div className="flex flex-col h-full p-3 gap-3">
      <div className="flex gap-2">
        <input
          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 placeholder-gray-600"
          placeholder="Ask something to benchmark..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runBenchmark()}
          disabled={loading}
        />
        <button
          className="px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors"
          onClick={runBenchmark}
          disabled={loading || !query.trim()}
        >
          {loading ? "Running..." : "Run"}
        </button>
      </div>

      <div className="flex gap-3 flex-1 overflow-hidden min-h-0">
        {/* MemoryOS arm */}
        <div className="flex-1 rounded-xl border border-gray-700 bg-gray-900/50 p-3 flex flex-col gap-2 overflow-y-auto scrollbar-thin">
          <div className="text-xs font-semibold text-blue-400">MemoryOS (Mem0g)</div>
          {loading ? (
            <div className="text-gray-600 text-xs animate-pulse">Running...</div>
          ) : result ? (
            <>
              <p className="text-gray-200 text-xs leading-relaxed whitespace-pre-wrap flex-1">
                {result.mem0g.response}
              </p>
              <div className="mt-auto space-y-0.5 text-[11px] text-gray-500 border-t border-gray-700 pt-2">
                <div>⚡ {result.mem0g.latencyMs}ms</div>
                <div>📝 {result.mem0g.tokensUsed.toLocaleString()} tokens</div>
                {result.mem0g.memoriesRetrieved != null && (
                  <div>🧠 {result.mem0g.memoriesRetrieved} memories</div>
                )}
              </div>
            </>
          ) : (
            <p className="text-gray-600 text-xs italic">Run a benchmark to see results.</p>
          )}
        </div>

        {/* Full-context arm */}
        <div className="flex-1 rounded-xl border border-gray-700 bg-gray-900/50 p-3 flex flex-col gap-2 overflow-y-auto scrollbar-thin">
          <div className="text-xs font-semibold text-orange-400">Full Context</div>
          {loading ? (
            <div className="text-gray-600 text-xs animate-pulse">Running...</div>
          ) : result ? (
            <>
              <p className="text-gray-200 text-xs leading-relaxed whitespace-pre-wrap flex-1">
                {result.fullContext.response}
              </p>
              <div className="mt-auto space-y-0.5 text-[11px] text-gray-500 border-t border-gray-700 pt-2">
                <div>⚡ {result.fullContext.latencyMs}ms</div>
                <div>📝 {result.fullContext.tokensUsed.toLocaleString()} tokens</div>
                {result.tokenSavingsPct > 0 && (
                  <div className="text-green-500">
                    ~{result.tokenSavingsPct.toFixed(0)}% token savings
                  </div>
                )}
              </div>
            </>
          ) : (
            <p className="text-gray-600 text-xs italic">Run a benchmark to see results.</p>
          )}
        </div>
      </div>

      {result && (
        <div className="flex items-center justify-between gap-2">
          <button
            onClick={() => vote("mem0g")}
            className="flex-1 py-2 text-xs bg-blue-900/50 hover:bg-blue-800/50 border border-blue-700 rounded-lg transition-colors"
          >
            ← MemoryOS was better
          </button>
          <button
            onClick={() => vote("fullContext")}
            className="flex-1 py-2 text-xs bg-orange-900/50 hover:bg-orange-800/50 border border-orange-700 rounded-lg transition-colors"
          >
            Full Context was better →
          </button>
        </div>
      )}

      <div className="text-center text-[11px] text-gray-600">
        {votes.total > 0
          ? `MemoryOS ${votes.mem0g} wins · Full-context ${votes.fullContext} wins · ${votes.total} total`
          : "No votes yet — run a benchmark first"}
      </div>
    </div>
  );
}
