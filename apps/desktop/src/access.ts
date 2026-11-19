/** What Smara may do on this PC right now, in the owner's words. Pure helpers for the Browser & computer page. */
export type AccessState = {
  selected: { expires: number; apps?: string[]; target?: { title?: string }; scope?: string } | null;
  control?: { paused?: boolean };
};

export function accessLine(state: AccessState | null, nowMs: number): { on: boolean; title: string; detail: string } {
  const grant = state?.selected;
  if (!grant || grant.expires * 1000 <= nowMs) {
    return { on: false, title: "Smara is not using your apps right now",
      detail: "When it needs to, it asks you in the chat. You just tap Allow there." };
  }
  const minutes = Math.max(1, Math.round((grant.expires * 1000 - nowMs) / 60000));
  const what = grant.target?.title || "your apps";
  const paused = state?.control?.paused ? " · Paused" : "";
  return { on: true, title: `Allowed: ${what}${paused}`,
    detail: `Ends in ${minutes} minute${minutes === 1 ? "" : "s"}. Sending, paying and deleting still ask you each time.` };
}
