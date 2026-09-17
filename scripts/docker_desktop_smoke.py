"""Run the reset-attested Docker GUI transport smoke."""
from __future__ import annotations

import time

from smara.isolated_desktop import DockerDesktopTransport, IsolatedDesktop, StaleDesktopObservation


def main() -> int:
    transport = DockerDesktopTransport.start()
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                desktop = IsolatedDesktop(transport, deadline_seconds=20, action_budget=8)
                observation = desktop.observe()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.5)
        assert observation["pixel_width"] == 1024 and observation["pixel_height"] == 768
        assert len(observation["screenshot_sha256"]) == 64
        accepted = desktop.act(observation["observation_id"], "click", x=40, y=40)
        assert accepted["accepted"]
        fresh = desktop.observe()
        try:
            desktop.act(observation["observation_id"], "click", x=40, y=40)
        except StaleDesktopObservation:
            pass
        else:
            raise AssertionError("stale Docker observation was accepted")
        hotkey = desktop.act(fresh["observation_id"], "hotkey", text="CTRL+L")
        assert hotkey["accepted"]
        desktop.cancel()
        print("DOCKER_DESKTOP_SMOKE_OK")
        return 0
    finally:
        transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
