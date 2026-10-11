export type ContextFile = { name: string; path: string; directory: boolean; size: number };
export type ProjectListing = { path: string; entries: ContextFile[]; truncated: boolean };

export function contextMessage(draft: string, files: ContextFile[]): string {
  if (!files.length) return draft;
  return `${draft}\n\nSelected project files (paths relative to the current project):\n${JSON.stringify(files.map(file => ({ path: file.path })), null, 2)}\nRead the relevant files using tools. Use image_analyze for image questions or document_ocr for scanned documents when needed; these tools ask before uploading to Sarvam. Do not invent unread contents.`;
}
