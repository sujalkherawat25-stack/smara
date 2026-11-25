/** Server name the runtime gives a connector: `conn_<id>`. */
export const isConnectorServer = (server: string) => /^conn_[a-z0-9_]+$/.test(server);

/** "conn_google_sheets" -> "Google sheets". Used when the connector's own label is not at hand. */
export function connectorLabel(server: string): string {
  const words = server.replace(/^conn_/, "").split("_").filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : "this service";
}

/** "mail_send" -> "mail send": a tool name read as words. */
export const toolWords = (tool: string) => tool.replace(/^mcp__conn_[a-z0-9_]+__/, "").replace(/[_-]+/g, " ").trim();

/** Plain-language wording for the approval card that takes the composer's place while Smara waits for the owner. */
export type AskView = { title: string; summary: string; detail: string; kind: "command" | "files" | "browser" | "computer" | "access" | "tool" | "team"; risky: boolean };

const RISKY = /\b(send|submit|pay|payment|delete|remove|publish|post|transfer|upload|sign in|login|log in|purchase|buy|confirm)\b/i;

const TOOL_WORDS: Record<string, string> = {
  browser_navigate: "open a page", browser_click: "click", browser_type: "type into a field", browser_fill_form: "fill a form",
  browser_select_option: "choose an option", browser_press_key: "press a key", browser_file_upload: "upload a file",
  browser_tabs: "switch tabs", browser_handle_dialog: "answer a page dialog", browser_drag: "drag on the page",
};

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Splits "Allow the playwright tool browser_click: Download (JSON)?" into the tool and what it acts on. */
function parseToolMessage(message: string): { tool: string; target: string } | null {
  const match = /tool\s+([A-Za-z0-9_.-]+)\s*:?\s*(.*?)\??$/s.exec(message);
  return match ? { tool: match[1], target: match[2].trim() } : null;
}

const TEAM_TITLES: Record<string, string> = { "Hire an employee": "team_hire", "Change an employee": "team_update", "Remember a rule": "team_remember", "Set up a routine": "team_routine", "Remove an employee": "team_remove", "Save a skill": "skill_save", "Forget a skill": "skill_forget" };
function toolFromTitle(title: string): string { return TEAM_TITLES[title] ?? ""; }

/** Team changes (hire, change, remember) in the owner's words: who, what, and the job text to check. */
function teamView(server: string, tool: string, params: Record<string, unknown>): AskView | null {
  if (server !== "smara_team" && !tool.startsWith("team_") && !tool.startsWith("skill_")) return null;
  const who = str(params.name) || str(params.employee);
  if (tool === "team_hire") return { kind: "team", title: `Hire ${who || "a new employee"}${str(params.role) ? ` as ${str(params.role)}` : ""}?`,
    summary: str(params.job), detail: "", risky: false };
  if (tool === "team_remember") return { kind: "team", title: `Should ${who || "this employee"} remember this from now on?`, summary: str(params.rule), detail: "", risky: false };
  if (tool.startsWith("skill_")) {
    // Skill tools name the employee by id ("kiran", "zoya-1a2b3c"); show it as a name.
    const id = str(params.employee).replace(/-[0-9a-f]{6}$/, "");
    const employee = id ? id.charAt(0).toUpperCase() + id.slice(1) : "";
    return skillView(tool, employee, params);
  }
  if (tool === "team_remove") return { kind: "team", title: `Remove ${who || "this employee"} from the team?`,
    summary: "Their past chats stay. You can hire someone again any time.", detail: "", risky: true };
  if (tool === "team_routine" && str(params.watch_for)) {
    const gap = { "15min": "15 minutes", "30min": "30 minutes", hour: "hour" }[str(params.every) as "hour"] ?? str(params.every);
    return { kind: "team", title: `Let ${who || "this employee"} watch for ${str(params.watch_for).slice(0, 80)}?`, summary: `${str(params.task)}

Checks every ${gap}.`,
      detail: "Stays quiet when nothing new matches; only a real finding reaches you. Read-only: anything that sends, pays or posts still asks you.", risky: false };
  }
  if (tool === "team_routine") {
    const when = { hour: "every hour", day: `every day at ${str(params.at) || "09:00"}`, week: `every week at ${str(params.at) || "09:00"}` }[str(params.every) as "hour"] ?? str(params.every);
    return { kind: "team", title: `Let ${who || "this employee"} do this ${when}?`, summary: str(params.task),
      detail: "Each run is read-only and reports back. Anything that sends, pays or posts still asks you.", risky: false };
  }
  if (tool === "team_update") {
    const changes = [str(params.name) && `name → ${str(params.name)}`, str(params.role) && `role → ${str(params.role)}`].filter(Boolean).join(", ");
    return { kind: "team", title: `Change ${str(params.employee) || "this employee"}?`, summary: [changes, str(params.job) && `New job: ${str(params.job)}`].filter(Boolean).join("\n\n"), detail: "", risky: false };
  }
  return null;
}

/** Teaching or forgetting a workflow: the owner checks the exact steps the employee will follow from now on. */
function skillView(tool: string, who: string, params: Record<string, unknown>): AskView | null {
  if (tool === "skill_save") {
    const steps = Array.isArray(params.steps) ? params.steps.map(String) : str(params.steps).split(/\r?\n/).filter(Boolean);
    const extra = [str(params.output) && `Output: ${str(params.output)}`,
      Array.isArray(params.never) && params.never.length ? `Never: ${params.never.map(String).join("; ")}` : "",
      str(params.change_note) && `Change: ${str(params.change_note)}`].filter(Boolean).join("\n");
    return { kind: "team", title: `Teach ${who || "this employee"} “${str(params.name) || "this workflow"}”?`,
      summary: [`Use when: ${str(params.when_to_use)}`, steps.map((step, i) => `${i + 1}. ${step}`).join("\n")].join("\n\n"),
      detail: extra, risky: false };
  }
  if (tool === "skill_forget") return { kind: "team", title: `Should ${who || "this employee"} forget “${str(params.name)}”?`,
    summary: "They will stop following this workflow. You can teach it again any time.", detail: "", risky: true };
  return null;
}

/** The agent asking for permission to use the owner's apps, in chat, with the time and apps spelled out. */
function accessView(params: Record<string, unknown>): AskView {
  const minutes = typeof params.minutes === "number" ? Math.max(5, Math.min(120, params.minutes)) : 30;
  const apps = Array.isArray(params.apps) ? params.apps.map(String).filter(Boolean) : [];
  return { kind: "computer", title: `Let Smara use ${apps.length ? apps.join(", ") : "your apps"} for ${minutes} minutes?`,
    summary: str(params.reason) || "Smara needs to open and use an app on this PC to do this.",
    detail: "It stops by itself after the time, or when you press Pause or Stop on the bar at the top of the screen (or Ctrl+Alt+Esc). "
      + "Sending, paying, deleting and signing in still ask you each time. It never types passwords, OTPs or card numbers.", risky: false };
}

/** An approval that sends a file off this PC, in words the owner can decide on (the phone shows the same); "" otherwise. */
export function uploadWarning(request: { method: string; params: Record<string, unknown> }): string {
  const p = request.params;
  if (request.method !== "mcpServer/elicitation/request" || str(p.serverName) !== "smara_readers") return "";
  const tool = /tool\s+"?([A-Za-z0-9_.-]+)"?/.exec(str(p.message))?.[1] ?? "";
  if (tool !== "image_analyze" && tool !== "document_ocr") return "";
  const meta = (p._meta && typeof p._meta === "object" ? p._meta : {}) as Record<string, unknown>;
  const given = (meta.tool_params && typeof meta.tool_params === "object" ? meta.tool_params : {}) as Record<string, unknown>;
  const path = str(given.path).slice(0, 300) || "an unnamed file";
  return tool === "image_analyze"
    ? `UPLOAD A PICTURE from your PC to Sarvam, an outside AI service, to read it: ${path}\nThe whole picture is sent, including any names, numbers or other private details on it. Sarvam may charge for this.`
    : `UPLOAD A FILE from your PC to Sarvam, an outside AI service, to read its text: ${path}\nThe whole document is sent, including any private details in it. Sarvam may charge for this.`;
}

export function askView(request: { method: string; params: Record<string, unknown> }): AskView {
  const p = request.params;
  const reason = str(p.reason);
  if (request.method === "item/commandExecution/requestApproval") {
    const command = Array.isArray(p.command) ? p.command.join(" ") : str(p.command);
    return { kind: "command", title: "Run a command on this PC?", summary: reason || "Smara wants to run this command.",
      detail: command.slice(0, 2000) + (str(p.cwd) ? `\n\nFolder: ${str(p.cwd)}` : ""), risky: RISKY.test(command) };
  }
  if (request.method === "item/fileChange/requestApproval") {
    return { kind: "files", title: "Change files in this project?", summary: reason || "Smara wants to save changes to project files.",
      detail: str(p.grantRoot) ? `Folder: ${str(p.grantRoot)}` : "", risky: false };
  }
  if (request.method === "item/permissions/requestApproval") {
    const wants = Object.keys((p.permissions as Record<string, unknown>) ?? {}).map(key => key === "network" ? "internet access" : key === "fileSystem" ? "access to more folders" : key);
    return { kind: "access", title: "Give Smara more access for this step?", summary: reason || `Smara asks for ${wants.join(" and ") || "more access"}.`, detail: "", risky: false };
  }
  const message = str(p.message);
  const server = str(p.serverName);
  const meta = (p._meta && typeof p._meta === "object" ? p._meta : {}) as Record<string, unknown>;
  const params = (meta.tool_params && typeof meta.tool_params === "object" ? meta.tool_params : {}) as Record<string, unknown>;
  const upload = uploadWarning(request);
  if (upload) return { kind: "tool", title: /A PICTURE/.test(upload) ? "Upload a picture to Sarvam?" : "Upload a file to Sarvam?",
    summary: upload, detail: str(params.question).slice(0, 2000), risky: true };
  const team = teamView(server, str(meta.tool_name) || toolFromTitle(str(meta.tool_title)), params);
  if (team) return team;
  if (str(meta.tool_name) === "computer_request_access" || /computer_request_access/.test(message)) return accessView(params);
  const parsed = parseToolMessage(message);
  if (isConnectorServer(server)) {
    const tool = str(meta.tool_name) || parsed?.tool || "";
    const words = toolWords(tool);
    const args = Object.entries(params).slice(0, 6).map(([key, value]) => `${key}: ${typeof value === "string" ? value.slice(0, 200) : JSON.stringify(value).slice(0, 200)}`).join(String.fromCharCode(10));
    return { kind: "tool", title: `Let ${connectorLabel(server)} ${words || "do this"}?`, summary: message || `${connectorLabel(server)} · ${words}`, detail: args, risky: RISKY.test(`${words} ${message}`) };
  }
  if (parsed && /playwright|browser/i.test(server + parsed.tool)) {
    const verb = TOOL_WORDS[parsed.tool] ?? parsed.tool.replace(/^browser_/, "").replace(/_/g, " ");
    return { kind: "browser", title: `Let Smara ${verb} in the browser?`, summary: parsed.target || message, detail: message, risky: RISKY.test(message) };
  }
  if (parsed && /computer/i.test(server + parsed.tool)) {
    return { kind: "computer", title: "Let Smara use an app on this PC?", summary: parsed.target || message, detail: message, risky: RISKY.test(message) };
  }
  return { kind: "tool", title: "Allow this step?", summary: message || request.method, detail: server ? `Tool: ${server}${parsed ? ` · ${parsed.tool}` : ""}` : "", risky: RISKY.test(message) };
}
