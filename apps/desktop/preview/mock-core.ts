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

const turnsFor = (id: string) => id === "t-stock" ? [{
  id: "turn-3", status: "completed",
  items: [
    { id: "u3", type: "userMessage", content: [{ type: "text", text: "Compare the stock sheet with the Tally export." }] },
    { id: "a3", type: "agentMessage", text: "Three rows differ: Gupta Kirana (4 units), Verma and Sons (2 units), and one duplicate line in the stock sheet." },
  ],
}] : id === "t-tally" ? [{
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
        if (message.method === "thread/resume" && message.params?.threadId === "t-tally") setTimeout(() => emit("native-runtime-event", { generation: args.generation, message: {
          id: "preview-ask-1", method: "mcpServer/elicitation/request", params: { threadId: message.params?.threadId, turnId: "turn-live", serverName: "playwright", mode: "form",
            message: "Allow the playwright tool browser_click: “Submit return” button on services.gst.gov.in/returns?", requestedSchema: { type: "object", properties: {} },
            _meta: { codex_approval_kind: "mcp_tool_call" } } } }), 600);
      }
      return undefined as T;
    }
    case "computer_bar_state": case "computer_bar_pause": case "computer_bar_stop":
      return { active: true, paused: command === "computer_bar_pause" && args.paused === true, until: Date.now() / 1000 + 1500, app: "tallyprime.exe",
        title: "Gateway of Tally", action: "press_key", last_ts: Date.now() / 1000, hotkey: "Ctrl+Alt+Esc", hotkey_ok: true } as T;
    case "native_manage": {
      const request = (args.request ?? {}) as Record<string, unknown>;
      if (request.operation === "audit") return auditPreview(request) as T;
      if (request.operation === "goals") return goalsPreview((request.goal ?? {}) as Record<string, unknown>) as T;
      if (request.operation === "hosted_memory") return { connected: true, shop: "Sharma Traders", enabled: request.action === "set" ? request.enabled === true : false } as T;
      if (request.operation === "hosted_approvals") return hostedPreview(request) as T;
      return setup as T;
    }
    case "native_project_files": return {
      path: "", truncated: false,
      entries: [
        { name: "Bills", path: "Bills", directory: true, size: 0 },
        { name: "Tally_export.xlsx", path: "Tally_export.xlsx", directory: false, size: 48213 },
        { name: "Sharma_statement.pdf", path: "Sharma_statement.pdf", directory: false, size: 120540 },
      ],
    } as T;
    case "bridge_state": return {
      paired: true, running: true, paused: false, shop: "Demo Shop", server: "https://ai.syntarus.com/smara",
      now: nowSeconds(), status: { online: true, last_poll_at: nowSeconds() - 8 },
    } as T;
    default: return {} as T;
  }
}

// Sample Activity log for the preview (Settings > Activity log).
const now = Date.now() / 1000;
const auditRows = [
  { id: "a6", ts: now - 20, source: "bridge", task: "j1", kind: "browser", action: "download", target: '[4] link "Download GSTR-2B"', detail: null, outcome: "ok", approval: "", screenshot: "shots/demo.jpg" },
  { id: "a5", ts: now - 60, source: "desktop", task: "t1", kind: "approval", action: "approved", target: "playwright", detail: null, outcome: "", approval: "owner", screenshot: "" },
  { id: "a4", ts: now - 70, source: "desktop", task: "t1", kind: "browser", action: "browser_click", target: "element=Search", detail: null, outcome: "completed ok", approval: "", screenshot: "" },
  { id: "a3", ts: now - 300, source: "desktop", task: "t2", kind: "computer", action: "computer_act", target: "window_id=TallyPrime", detail: null, outcome: "completed typed 3 characters", approval: "", screenshot: "" },
  { id: "a2", ts: now - 400, source: "bridge", task: "j0", kind: "browser", action: "fill", target: '[7] textbox "GSTIN"', detail: null, outcome: "error: the owner paused Smara in the browser (Stop button)", approval: "", screenshot: "" },
  { id: "a1", ts: now - 900, source: "bridge", task: "j0", kind: "browser", action: "navigate", target: "https://services.gst.gov.in", detail: null, outcome: "ok", approval: "", screenshot: "" },
];
const tinyImage = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";
function auditPreview(request: Record<string, unknown>) {
  if (request.action === "days") return { days: [{ day: new Date().toISOString().slice(0, 10), entries: auditRows.length, bytes: 2400 }], folder: "C:/Users/owner/AppData/Roaming/Smara/audit", keep_days: 30 };
  if (request.action === "list") return { entries: auditRows };
  if (request.action === "shot") return { png_base64: tinyImage };
  return { deleted: request.day ?? "all", days: [] };
}

// Sample tasks for the preview task board.
const sampleGoal = (id: string, state: string, objective: string, agent: string, detail: string) => ({
  id, state, session_id: null, lease: state === "running" || state === "awaiting_approval" ? "lease" : null, updated: Date.now() / 1000,
  cycles: 2, requests: 5, token_estimate: 12000, detail,
  checkpoint: { summary: "Read the October purchase register (212 invoices).", next_step: "Open the GSTR-2B download page." },
  spec: { objective, success_criteria: "A sheet of mismatches", workspace: "C:/Users/owner/Documents/Sharma Traders", profile_id: "glm", sandbox: "read-only",
    agent_id: agent, use_apps: agent === "kiran", max_cycles: 8, max_requests: 30, max_token_estimate: 500000 } });
const sampleGoals = [
  sampleGoal("t1", "awaiting_approval", "Download the GSTR-2B for October from the GST portal and compare it with the purchase register", "scout", "Finished browser click"),
  sampleGoal("t2", "running", "Post the day book totals from Tally into the monthly Excel summary", "kiran", "Started computer act"),
  sampleGoal("t3", "queued", "Draft payment reminders for dues older than 60 days", "rohan", ""),
  sampleGoal("t4", "completed", "Read the three bank statement PDFs and list failed rows", "nisha", "Accepted by user"),
];
function goalsPreview(goal: Record<string, unknown>) {
  if (goal.action === "get") {
    const found = sampleGoals.find(item => item.id === goal.id) ?? sampleGoals[0];
    return { ...found, events: [{ seq: 1, kind: "activity", created: 0, body: { summary: "Starting cycle 2 of 8" } }, { seq: 2, kind: "activity", created: 0, body: { summary: found.detail || "Queued" } }],
      approvals: found.state === "awaiting_approval" ? [{ id: "r1", method: "mcpServer/elicitation/request", params: { mode: "form", serverName: "playwright",
        message: "Allow the playwright tool browser_click: Download GSTR-2B (JSON)?", requestedSchema: { type: "object", properties: {} }, _meta: { codex_approval_kind: "mcp_tool_call" } } }] : [] };
  }
  return { goals: sampleGoals, service: { running: true }, max_parallel: 3, parallel_limit: 4,
    input: { holder: "h", label: "Kiran: Post the day book totals", task: "t2", pid: 1, until: Date.now() / 1000 + 15 } };
}

// Sample hosted (Telegram) approvals for the preview inbox.
function hostedPreview(request: Record<string, unknown>) {
  const status = { connected: true, shop: "Sharma Traders", server: "https://ai.syntarus.com/smara" };
  if (request.action === "decide") return { outcome: "done" };
  if (request.action !== "list") return status;
  return { ...status, approvals: [
    { id: "aaaaaaaa-1111-2222-3333-444444444444", tool: "browser_step", card: "Smara wants to click the “File return” button on services.gst.gov.in/returns (Returns Dashboard).\n\nApprove?",
      step: { action: "click", ref: "41" }, site_choice: true, asked_at: new Date(Date.now() - 120000).toISOString() },
    { id: "bbbbbbbb-1111-2222-3333-444444444444", tool: "pc_files", card: "Save this file on your PC (Smara Shared folder)?\n\nreports/october-dues.csv",
      step: { action: "write", path: "reports/october-dues.csv" }, site_choice: false, asked_at: new Date(Date.now() - 20000).toISOString() },
  ] };
}

