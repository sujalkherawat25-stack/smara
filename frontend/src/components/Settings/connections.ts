/**
 * Settings/connections.ts — channel connection registry.
 *
 * Each Connection declares the metadata needed for its settings row. The
 * panel component owns its own data fetching + UI (link flow, status, etc.)
 * since each channel has a different connect/disconnect flow.
 *
 * To add a new channel (WhatsApp / Discord / Slack):
 *   1. Add an entry here with id matching the backend's channel_links.channel value.
 *   2. Add a panel component and wire it into ConnectionsSection's switch.
 */

export interface Connection {
  id: string;
  label: string;
  tagline: string;
  comingSoon?: boolean;
}

export const CONNECTIONS: Connection[] = [
  {
    id: "telegram",
    label: "Telegram",
    tagline: "Chat with Smara on Telegram with the same memory as the web app",
  },
  {
    id: "whatsapp",
    label: "WhatsApp",
    tagline: "Coming soon — WhatsApp Business API integration",
    comingSoon: true,
  },
];
