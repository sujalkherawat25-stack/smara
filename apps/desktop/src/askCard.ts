/** Plain-language wording for the approval card that takes the composer's place while Smara waits for the owner. */
export type AskView = { title: string; summary: string; detail: string; kind: "command" | "files" | "browser" | "computer" | "access" | "tool"; risky: boolean };

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
  const parsed = parseToolMessage(message);
  if (parsed && /playwright|browser/i.test(server + parsed.tool)) {
    const verb = TOOL_WORDS[parsed.tool] ?? parsed.tool.replace(/^browser_/, "").replace(/_/g, " ");
    return { kind: "browser", title: `Let Smara ${verb} in the browser?`, summary: parsed.target || message, detail: message, risky: RISKY.test(message) };
  }
  if (parsed && /computer/i.test(server + parsed.tool)) {
    return { kind: "computer", title: "Let Smara use an app on this PC?", summary: parsed.target || message, detail: message, risky: RISKY.test(message) };
  }
  return { kind: "tool", title: "Allow this step?", summary: message || request.method, detail: server ? `Tool: ${server}${parsed ? ` · ${parsed.tool}` : ""}` : "", risky: RISKY.test(message) };
}
