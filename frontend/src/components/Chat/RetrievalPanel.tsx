import type { RetrievalTrace, RetrievalResult } from "@/types/memory";
import clsx from "clsx";

interface Props {
  trace: RetrievalTrace;
}

function ScoreBar({ score }: { score: number }) {
  return (
    <div className="w-16 h-1.5 bg-gray-700 rounded-full overflow-hidden shrink-0">
      <div
        className="h-full bg-blue-500 rounded-full"
        style={{ width: `${Math.min(score * 100, 100)}%` }}
      />
    </div>
  );
}

function ResultRow({ result }: { result: RetrievalResult }) {
  return (
    <div
      className={clsx(
        "pl-2 border-l-2 space-y-0.5",
        result.used ? "border-green-500" : "border-gray-700 opacity-60"
      )}
    >
      <div className="flex items-center gap-2">
        <ScoreBar score={result.fusedScore} />
        <span className="text-gray-200 truncate flex-1">{result.text}</span>
        {result.used && <span className="text-green-400 shrink-0 text-xs">✓ used</span>}
      </div>
      <div className="text-gray-500 text-xs">
        sem {result.semanticScore != null ? result.semanticScore.toFixed(2) : "—"} ·{" "}
        ent {result.entityScore != null ? result.entityScore.toFixed(2) : "—"} ·{" "}
        fused {result.fusedScore.toFixed(2)}
      </div>
      {result.entityPath.length > 0 && (
        <div className="text-gray-600 text-xs">via: {result.entityPath.join(" → ")}</div>
      )}
    </div>
  );
}

export default function RetrievalPanel({ trace }: Props) {
  const memoriesUsed = trace.results.filter((r) => r.used).length;

  return (
    <div className="ml-1 mt-1 rounded-lg border border-gray-700 bg-gray-900/60 p-3 text-xs space-y-2 w-full max-w-[85%]">
      <div className="text-gray-400 font-medium flex gap-2">
        <span>{trace.candidatesChecked} checked</span>
        <span>·</span>
        <span>{memoriesUsed} used</span>
        <span>·</span>
        <span>{trace.latencyMs}ms</span>
      </div>
      {trace.results.length === 0 ? (
        <div className="text-gray-600">No memories retrieved</div>
      ) : (
        trace.results.map((r) => <ResultRow key={r.memoryId} result={r} />)
      )}
    </div>
  );
}
