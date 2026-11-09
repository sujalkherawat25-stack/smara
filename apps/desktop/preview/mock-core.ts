// Preview only: sample data in place of the Tauri backend, so the real UI can be seen and screenshotted in a browser.
import { emit } from "./mock-event";

const WORKSPACE = "C:/Users/sujal/Work";
const nowSeconds = () => Date.now() / 1000;

const setup = {
  preferences: {
    projects: [{ workspace: WORKSPACE, label: "Demo Shop" }],
    workspace: WORKSPACE, active_model: "glm", search_provider: "tavily",
  },
  profiles: [{ id: "glm", label: "glm5.3", provider: "custom", base_url: "https://example.test/v1", model: "glm5.3",
    auth_header: "authorization", credential_name: "GLM_KEY", updated_at: "" }],
  credentials: [],
  search: { provider: "tavily", configured: true, network_tested: false, detail: "" },
  memory: { enabled: false, user_id: "", scope: "project", configured: false, sdk_available: false, endpoint: "",
    network_tested: false, detail: "", writes: {}, last_activity: null },
  skills_roots: [],
  computer: { enabled: false, expires: null },
  agents: {
    roster: [
      { id: "kiran", name: "Kiran", role: "Accounts", icon: "📒", instructions: "", preset: true },
      { id: "rohan", name: "Rohan", role: "Dues", icon: "🔔", instructions: "", preset: true },
      { id: "scout", name: "Scout", role: "Web tasks", icon: "🧭", instructions: "", preset: true },
      { id: "nisha", name: "Nisha", role: "Documents", icon: "📄", instructions: "", preset: true },
    ],
    default: "kiran",
    assignments: { "t-dues": "rohan" },
  },
};

const threads = [
  { id: "t-tally", name: "Who owes the most this month", preview: "Who owes the most this month", updatedAt: nowSeconds() - 3600 },
  { id: "t-stock", name: "Stock vs Tally sheet", preview: "Stock vs Tally sheet", updatedAt: nowSeconds() - 86400 },
  { id: "t-dues", name: "Reminder for Sharma Traders", preview: "Reminder for Sharma Traders", updatedAt: nowSeconds() - 7200 },
];

const turnsFor = (id: string) => id === "t-tally" ? [{
  id: "turn-1", status: "completed",
  items: [
    { id: "u1", type: "userMessage", content: [{ type: "text", text: "Read the Tally export and tell me who owes the most." }] },
    { id: "c1", type: "commandExecution", command: "python read_tally.py Tally_export.xlsx", cwd: WORKSPACE, status: "completed", exitCode: 0, durationMs: 3200, aggregatedOutput: "Loaded 42 rows\nTotal outstanding: 4,37,201" },
    { id: "c2", type: "commandExecution", command: "python group_by_party.py", cwd: WORKSPACE, status: "completed", exitCode: 0, durationMs: 740, aggregatedOutput: "Sharma Traders 1,20,000\nGupta Kirana 84,500" },
    { id: "c3", type: "commandExecution", command: "python check_dates.py --overdue", cwd: WORKSPACE, status: "failed", exitCode: 1, durationMs: 410, aggregatedOutput: "KeyError: 'due_date'" },
    { id: "a1", type: "agentMessage", text: "**Sharma Traders** owes the most: ₹1,20,000, of which ₹45,000 is more than 30 days late.\n\nGupta Kirana follows with ₹84,500. I could not read the due dates for one sheet, so that total may change." },
  ],
}] : [{
  id: "turn-2", status: "completed",
  items: [
    { id: "u2", type: "userMessage", content: [{ type: "text", text: "Draft a reminder for Sharma Traders." }] },
    { id: "a2", type: "agentMessage", text: "Namaste Sharma ji, ₹45,000 from the invoice of 3 Oct is pending. Could you share a payment date? Thank you." },
  ],
}];

function response(method: string, params: Record<string, unknown>): unknown {
  switch (method) {
    case "initialize": return { serverInfo: { name: "preview" } };
    case "thread/list": return { data: threads, nextCursor: null };
    case "thread/read":
    case "thread/resume": {
      const id = String(params.threadId ?? "t-tally");
      return { thread: { id, cwd: WORKSPACE, turns: turnsFor(id) } };
    }
    case "thread/start": return { thread: { id: "t-new", name: null, preview: "" } };
    case "windowsSandbox/readiness": return { status: "ready" };
    case "turn/start": return { turn: { id: "turn-live", status: "inProgress" } };
    default: return {};
  }
}

export async function invoke<T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  switch (command) {
    case "native_status": return { built: true, windowsTerminalBuild: true, sourceOwned: true } as T;
    case "native_start": return { generation: "preview-1", sourceOwned: true } as T;
    case "native_stop": return undefined as T;
    case "native_send": {
      const message = args.message as { id?: number | string; method?: string; params?: Record<string, unknown> };
      if (message?.id !== undefined && message.method) {
        const result = response(message.method, message.params ?? {});
        setTimeout(() => emit("native-runtime-event", { generation: args.generation, message: { id: message.id, result } }), 5);
      }
      return undefined as T;
    }
    case "native_manage": return setup as T;
    case "native_project_files": return {
      path: "", truncated: false,
      entries: [
        { name: "Bills", path: "Bills", directory: true, size: 0 },
        { name: "Tally_export.xlsx", path: "Tally_export.xlsx", directory: false, size: 48213 },
        { name: "Sharma_statement.pdf", path: "Sharma_statement.pdf", directory: false, size: 120540 },
      ],
    } as T;
    case "bridge_state": return {
      paired: true, running: true, paused: false, shop: "Demo Shop", server: "https://ai.syntarus.com/munim",
      now: nowSeconds(), status: { online: true, last_poll_at: nowSeconds() - 8 },
    } as T;
    default: return {} as T;
  }
}
