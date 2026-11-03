"""Run inside the app image against a live munim_sandbox container (container mode).

    docker run --rm -v <jobs-volume>:/jobs -e SANDBOX_MODE=container smara-munim python tests/sandbox_escape_probe.py

Each probe is code an attacker-controlled prompt could make the agent run. A probe
passes when the sandbox prevents it. Exit code 1 if any isolation guarantee fails.
"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")
from src.box.sandbox import Sandbox  # noqa: E402

PROBES = {
    "network_blocked": ("import socket\ns=socket.create_connection(('1.1.1.1', 443), timeout=3)\nprint('CONNECTED')",
                        lambda r: "CONNECTED" not in r["stdout"]),
    "dns_blocked": ("import socket\nprint(socket.gethostbyname('ai.syntarus.com'))", lambda r: r["exit_code"] != 0),
    "runs_as_nobody": ("import os\nRESULT={'uid': os.getuid(), 'gid': os.getgid()}",
                       lambda r: r["result"] == {"uid": 65534, "gid": 65534}),
    "cannot_list_jobs": ("import os\nprint(os.listdir('/jobs'))", lambda r: r["exit_code"] != 0),
    "cannot_read_other_job": (None, lambda r: "SECRET-OTHER-TENANT" not in r["stdout"]),
    "root_fs_read_only": ("open('/srv/pwned.txt','w').write('x')", lambda r: r["exit_code"] != 0),
    "cannot_touch_runner": ("open('/srv/runner.py','a').write('#x')", lambda r: r["exit_code"] != 0),
    "memory_capped": ("x = bytearray(3_000_000_000)\nprint('ALLOCATED')", lambda r: "ALLOCATED" not in r["stdout"]),
    "no_privilege_regain": ("import os\nos.setuid(0)\nprint('ROOT')", lambda r: "ROOT" not in r["stdout"]),
    "fork_bomb_contained": ("import os\nfor _ in range(2000):\n    try:\n        os.fork()\n    except OSError:\n        break\nprint('alive')",
                            lambda r: True),   # must not take the runner down; checked by the next probe
    "still_serving_after_abuse": ("print(2 + 2)", lambda r: r["stdout"].strip() == "4"),
}


async def main() -> int:
    sb = Sandbox("/jobs", "container")
    victim = await sb.run("open('out/secret.txt','w').write('SECRET-OTHER-TENANT')", {"secret.csv": b"SECRET-OTHER-TENANT"})
    vid = victim["job_id"]
    failures = 0
    for name, (code, ok) in PROBES.items():
        if code is None:
            code = (f"for p in ['/jobs/{vid}/out/secret.txt', '/jobs/{vid}/in/secret.csv', '/jobs/{vid}/code.py']:\n"
                    "    try:\n        print(open(p).read())\n    except Exception as e:\n        print('denied', type(e).__name__)")
        r = await sb.run(code, {}, timeout_s=15)
        passed = bool(ok(r))
        failures += not passed
        print(f"{'OK  ' if passed else 'FAIL'} {name:<26} exit={r.get('exit_code')} "
              f"{(r.get('stdout') or '').strip()[:60]!r} {(r.get('stderr') or '').strip().splitlines()[-1:]!s:.80}")
    print(json.dumps({"failures": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
