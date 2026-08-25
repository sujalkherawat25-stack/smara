/** Make model-produced Mermaid flowcharts safe for the browser parser. */

export interface PreparedMermaid {
  source: string;
  changed: boolean;
}

const FLOWCHART_HEAD = /^(flowchart|graph)(?:\s+(TB|BT|RL|LR|TD))?\s*$/i;
const NODE = /([A-Za-z_][\w-]*)\s*(\[\[.*?\]\]|\(\(.*?\)\)|\{\{.*?\}\}|\{.*?\}|\[.*?\]|\(.*?\))/g;

function escapeLabel(label: string): string {
  return label
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/\r?\n/g, " ")
    .trim();
}

function quoteNode(_match: string, id: string, shape: string): string {
  let open = "[";
  let close = "]";
  if (shape.startsWith("[[")) [open, close] = ["[[", "]]" ];
  else if (shape.startsWith("((")) [open, close] = ["((", "))" ];
  else if (shape.startsWith("{{")) [open, close] = ["{{", "}}" ];
  else if (shape.startsWith("{")) [open, close] = ["{", "}" ];
  else if (shape.startsWith("(")) [open, close] = ["(", ")" ];

  let label = shape.slice(open.length, shape.length - close.length).trim();
  if (label.startsWith('"') && label.endsWith('"')) label = label.slice(1, -1);
  return `${id}${open}"${escapeLabel(label)}"${close}`;
}

export function prepareMermaidSource(input: string): PreparedMermaid {
  const original = input || "";
  let source = original.trim();
  if (source.startsWith("```")) {
    source = source.replace(/^```(?:mermaid)?\s*/i, "").replace(/\s*```$/, "").trim();
  }

  const lines = source.split(/\r?\n/);
  const first = lines.findIndex((line) => line.trim() && !line.trim().startsWith("%%"));
  const head = first >= 0 ? lines[first].trim() : "";
  const match = head.match(FLOWCHART_HEAD);
  if (!match) return { source, changed: source !== original.trim() };

  let changed = source !== original.trim();
  const prepared = lines.map((line, index) => {
    if (index === first) {
      if (match[1].toLowerCase() === "graph") {
        changed = true;
        return `flowchart ${match[2] || "TD"}`;
      }
      return line;
    }
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("%%") || trimmed.startsWith("style ") || trimmed.startsWith("class ")) return line;
    const withoutTerminator = line.replace(/;\s*$/, "");
    const normalized = withoutTerminator.replace(NODE, quoteNode);
    if (normalized !== line) changed = true;
    return normalized;
  });
  return { source: prepared.join("\n"), changed };
}

