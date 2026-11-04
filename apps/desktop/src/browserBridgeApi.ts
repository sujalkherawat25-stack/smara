import { invoke } from "@tauri-apps/api/core";
import type { BridgeState, Components } from "./browserBridgeView";

export const bridgeState = () => invoke<BridgeState>("bridge_state");
export const bridgeCheck = () => invoke<Components>("bridge_check");
export const bridgeEnsure = () => invoke<BridgeState>("bridge_ensure");
export const bridgeStop = () => invoke<BridgeState>("bridge_stop");
export const bridgePair = (server: string, code: string) => invoke<BridgeState>("bridge_pair", { server, code });
export const bridgeUnpair = () => invoke<BridgeState>("bridge_unpair");
export const bridgeInstallComponents = () => invoke<Components>("bridge_install_components");
