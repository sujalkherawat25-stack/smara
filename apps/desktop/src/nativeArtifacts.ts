const PREVIEWABLE_EXTENSIONS = new Set([
  "bat", "bash", "c", "cpp", "csv", "css", "docx", "gif", "go", "h", "html", "htm", "ini", "java",
  "jpeg", "jpg", "js", "json", "jsonl", "jsx", "log", "md", "markdown", "mjs", "pdf", "png", "ps1",
  "py", "pptx", "rs", "sh", "sql", "toml", "ts", "tsx", "tsv", "txt", "webp", "xml", "yaml", "yml", "xlsx", "odt", "ods", "odp",
]);

/** Only offers a preview affordance for plausible local file references.
 * The native command still enforces the selected-project boundary. */
export function previewableLocalPath(source: string): string | null {
  let value = source.trim();
  if (!value || value.length > 1024 || /[\r\n\0]/.test(value)) return null;
  if (/^["'].*["']$/.test(value)) value = value.slice(1, -1).trim();
  if (!value || /:\/\//.test(value) || value.startsWith("\\\\") || value.startsWith("//")) return null;
  if (/[<>"|?*]/.test(value) || (value.includes(":") && !/^[a-z]:[\\/]/i.test(value))) return null;
  const name = value.split(/[\\/]/).at(-1) ?? "";
  const extension = name.split(".").at(-1)?.toLowerCase() ?? "";
  return PREVIEWABLE_EXTENSIONS.has(extension) ? value : null;
}
