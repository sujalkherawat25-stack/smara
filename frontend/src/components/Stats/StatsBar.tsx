import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { useChatStore } from "@/stores/chatStore";
import type { Stats } from "@/types/memory";

function Stat({ icon, label, value }: { icon: string; label: string; value: string | number }) {
  return (
    <span className="flex items-center gap-1 shrink-0">
      <span className="text-gray-600">{icon}</span>
      <span className="text-gray-500">{label}:</span>
      <span className="text-gray-300 font-mono">{value}</span>
    </span>
  );
}

export default function StatsBar() {
  const conversationId = useChatStore((s) => s.conversationId);
  const { data: stats } = useQuery<Stats>({
    queryKey: ["stats", conversationId],
    queryFn: () =>
      apiClient.get<Stats>("/v1/stats", { conversation_id: conversationId }),
    refetchInterval: 10_000,
    retry: false,
  });

  return (
    <div className="flex items-center gap-4 px-4 py-1.5 border-b border-gray-800 bg-gray-950 text-[11px] text-gray-400 overflow-x-auto shrink-0">
      <Stat icon="●" label="memories" value={stats?.totalMemories ?? 0} />
      <Stat icon="⬡" label="entities" value={stats?.totalEntities ?? 0} />
      <Stat icon="↔" label="relations" value={stats?.totalRelationships ?? 0} />
      <Stat icon="◈" label="density" value={(stats?.graphDensity ?? 0).toFixed(3)} />
      <Stat
        icon="⊕"
        label="hit rate"
        value={`${((stats?.memoryHitRate ?? 0) * 100).toFixed(0)}%`}
      />
      <Stat
        icon="⚡"
        label="avg latency"
        value={stats?.avgRetrievalLatencyMs ? `${stats.avgRetrievalLatencyMs}ms` : "—"}
      />
      <Stat
        icon="💾"
        label="token savings"
        value={stats?.tokenSavingsPct ? `~${stats.tokenSavingsPct.toFixed(0)}%` : "—"}
      />
    </div>
  );
}
