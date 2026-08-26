/**
 * MaintenanceScreen — shown when authStore.status === "unreachable".
 *
 * Distinct from SignInScreen: this fires when the backend is mid-deploy
 * (502/503/network failure on /v1/auth/me), not when the user genuinely
 * has no session. authStore already retries loadAccount() on an interval
 * while status stays "unreachable" — this screen just needs to sit calmly
 * until that resolves, so it carries no retry logic of its own.
 */

import SmaraLogo from "@/components/SmaraLogo";
export default function MaintenanceScreen() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col items-center justify-center px-6">
      <div className="flex flex-col items-center gap-6 max-w-md text-center">
        <SmaraLogo size={64} animate />

        <div className="space-y-2">
          <h1 className="text-2xl font-bold font-display">Server not connected</h1>
          <p className="text-gray-400 text-sm">
            Trying to reconnect automatically. Please wait a moment.
          </p>
        </div>

        <div className="flex items-center gap-2 text-gray-600 text-xs">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
          Reconnecting…
        </div>

      </div>
    </div>
  );
}
