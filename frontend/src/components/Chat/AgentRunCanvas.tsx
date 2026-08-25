import { Brain, Eye, FileText, Search, Sparkles, Zap } from "lucide-react";
import { useChatStore } from "@/stores/chatStore";

const agents = [
  { key: "memory", label: "Memory", icon: Brain },
  { key: "search", label: "Search", icon: Search },
  { key: "document", label: "Documents", icon: FileText },
  { key: "vision", label: "Vision", icon: Eye },
  { key: "action", label: "Actions", icon: Zap },
];

function activeKeys(activity: ReturnType<typeof useChatStore.getState>["activity"], phase: string | null) {
  const keys = new Set<string>();
  if (phase === "retrieve") { keys.add("memory"); keys.add("search"); }
  if (phase === "reason_act") keys.add("action");
  for (const item of activity) {
    if (item.kind === "memory_search") keys.add("memory");
    if (item.kind === "tool_call") {
      if (item.name.includes("search") || item.name.includes("weather") || item.name.includes("news")) keys.add("search");
      if (item.name.includes("document") || item.name.includes("pdf") || item.name.includes("read_")) keys.add("document");
      if (item.name.includes("image") || item.name.includes("vision")) keys.add("vision");
      if (!item.name.includes("search")) keys.add("action");
    }
  }
  return keys;
}

export default function AgentRunCanvas() {
  const phase = useChatStore((s) => s.currentPhase);
  const activity = useChatStore((s) => s.activity);
  const active = activeKeys(activity, phase);
  return (
    <div className="mx-auto mb-3 max-w-3xl rounded-2xl px-4 py-3" style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)" }}>
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={14} style={{ color: "var(--accent)" }} />
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Smara is working</span>
        <span className="text-[11px] ml-auto" style={{ color: "var(--text-muted)" }}>Coordinating agents</span>
      </div>
      <div className="grid grid-cols-5 gap-2">
        {agents.map(({ key, label, icon: Icon }) => {
          const on = active.has(key);
          return <div key={key} className="flex flex-col items-center gap-1 rounded-xl py-2 transition-all" style={{ background: on ? "var(--accent-dim)" : "var(--bg-elevated)", color: on ? "var(--accent)" : "var(--text-muted)", opacity: on ? 1 : 0.62 }}>
            <Icon size={16} className={on ? "animate-pulse" : ""} />
            <span className="text-[10px]">{label}</span>
          </div>;
        })}
      </div>
    </div>
  );
}
