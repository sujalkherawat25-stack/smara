/**
 * Owner channels on the hosted server, from its public health page (`/smara/healthz`, the `channels` block).
 * Counts and flags only: no names, numbers or tokens. Telegram is the owner's working channel. The WhatsApp link
 * is off unless the server enables it, and the official WhatsApp number is shown as configured or not set up.
 */
export type ChannelHealth = {
  channels?: {
    telegram?: { bot?: boolean; owners_linked?: number };
    whatsapp_link?: { enabled?: boolean };
    whatsapp_official?: { configured?: boolean };
  };
} | null | undefined;

export type ChannelLine = { label: string; tone: "ok" | "warn" | "off" };

/** The rows the Hosted panel shows. `undefined` means still loading; `null` means the server did not answer. */
export function channelLines(health: ChannelHealth): ChannelLine[] {
  if (health === undefined) return [{ label: "Channels: checking…", tone: "off" }];
  if (health === null) return [{ label: "Channels: server not reachable", tone: "warn" }];
  const c = health.channels;
  if (!c) return [{ label: "Channels: not reported by this server", tone: "off" }];
  const telegram = c.telegram ?? {};
  const linked = telegram.owners_linked ?? 0;
  const lines: ChannelLine[] = [];
  if (!telegram.bot) lines.push({ label: "Telegram: not set up", tone: "off" });
  else if (linked > 0) lines.push({ label: `Telegram: ${linked} owner${linked === 1 ? "" : "s"} linked`, tone: "ok" });
  else lines.push({ label: "Telegram: bot on, no owner linked", tone: "warn" });
  lines.push(c.whatsapp_link?.enabled
    ? { label: "WhatsApp link: on", tone: "ok" }
    : { label: "WhatsApp link: off", tone: "off" });
  lines.push(c.whatsapp_official?.configured
    ? { label: "WhatsApp (official number): configured", tone: "warn" }
    : { label: "WhatsApp (official number): not set up", tone: "off" });
  return lines;
}

const DEFAULT_HEALTH_URL = "https://ai.syntarus.com/smara/healthz";

/** The health page of the server this PC is paired with (any path on it), or the default hosted server. */
export function healthUrl(server?: string): string {
  if (!server) return DEFAULT_HEALTH_URL;
  try {
    return `${new URL(server).origin}/smara/healthz`;
  } catch {
    return DEFAULT_HEALTH_URL;
  }
}
