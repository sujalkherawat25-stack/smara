export const TERMINAL_DISPLAY_LIMIT = 500_000;
export const TERMINAL_OUTPUT_LIMIT = 8 * 1024 * 1024;

export type TerminalSession = {
  id: string;
  status: "starting" | "running" | "stopping" | "exited" | "failed" | "stopped";
  output: string;
  truncated: boolean;
  exitCode?: number;
  error?: string;
};

/** Fixed shell plus an explicit, no-network workspace-write sandbox. */
export function terminalExecParams(workspace: string, processId: string, cols = 100, rows = 28) {
  return {
    // The GUI already edits complete input lines. PSReadLine would also edit
    // them and attempt to read/write the user's personal history outside MXC.
    command: ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command",
      "if (Get-Module PSReadLine) { Remove-Module PSReadLine -ErrorAction Stop }"],
    processId,
    tty: true,
    streamStdin: true,
    streamStdoutStderr: true,
    disableTimeout: true,
    outputBytesCap: TERMINAL_OUTPUT_LIMIT,
    cwd: workspace,
    size: { cols, rows },
    sandboxPolicy: { type: "workspaceWrite", writableRoots: [workspace], networkAccess: false },
  };
}

export function encodeTerminalInput(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function decodeTerminalOutput(value: string, decoder: TextDecoder, stream = true): string {
  const binary = atob(value);
  const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
  const decoded = decoder.decode(bytes, { stream });
  return decoded
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001b\][^\u0007]*(?:\u0007|\u001b\\)/g, "")
    .replace(/\u001b[@-_]/g, "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
}

export function appendTerminalOutput(current: TerminalSession, delta: string, capReached = false): TerminalSession {
  let output = current.output + delta;
  let truncated = current.truncated || capReached;
  if (output.length > TERMINAL_DISPLAY_LIMIT) {
    output = output.slice(-TERMINAL_DISPLAY_LIMIT);
    truncated = true;
  }
  return { ...current, status: current.status === "starting" ? "running" : current.status, output, truncated };
}
