/** Connectors the owner plugged in (Settings > Connectors): view helpers and the ask card for their tools. */

export type Connector = { id: string; label: string; kind: "http" | "stdio"; url: string; command: string; args: string[]; enabled: boolean; has_token: boolean; env_names: string[] };
export type ConnectorList = { connectors: Connector[]; limit: number };

/** What a connector's address or command looks like in the list. */
export function connectorTarget(item: Connector): string {
  if (item.kind === "http") { try { return new URL(item.url).host; } catch { return item.url; } }
  return [item.command, ...item.args].join(" ").slice(0, 80);
}

/** Why the Add button is off, or "" when the form is complete. */
export function connectorProblem(form: { label: string; kind: "http" | "stdio"; url: string; command: string }): string {
  if (!form.label.trim()) return "Give it a name, like Gmail.";
  if (form.kind === "http") return /^https:\/\/[^\s/]+/i.test(form.url.trim()) || /^http:\/\/(127\.0\.0\.1|localhost)[:/]/i.test(form.url.trim()) ? "" : "Paste the service's https address.";
  return form.command.trim() ? "" : "Enter the command that starts it, like npx.";
}

/** Splits an arguments line into a list, keeping quoted parts together. */
export function splitArgs(line: string): string[] {
  const out: string[] = [];
  for (const match of line.matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)) out.push(match[1] ?? match[2] ?? match[3]);
  return out;
}
