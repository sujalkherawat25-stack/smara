import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { BridgeConfig, BridgeEvent, BridgeState } from "./browserBridgeView";

export type BridgeCheck = { python: boolean; playwright: boolean; browsers: Record<string, boolean> };
export const bridgeState = () => invoke<BridgeState>("bridge_state");
export const bridgePair = (server: string, code: string, name?: string) => invoke<BridgeState>("bridge_pair", { server, code, name });
export const bridgeUnpair = () => invoke<BridgeState>("bridge_unpair");
export const bridgeStart = () => invoke<BridgeState>("bridge_start");
export const bridgeStop = () => invoke<BridgeState>("bridge_stop");
export const bridgePause = (paused: boolean) => invoke<BridgeState>("bridge_pause", { paused });
export const bridgeSaveOptions = (config: BridgeConfig) => invoke<BridgeState>("bridge_save_options", { config });
export const bridgeCheck = () => invoke<BridgeCheck>("bridge_check");
export const onBridgeEvent = (handler: (event: BridgeEvent) => void): Promise<UnlistenFn> =>
  listen<BridgeEvent>("browser-bridge-event", message => handler(message.payload));
