"""Exercise the actual frozen executor without a checkout or Python on PATH."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


PROBE = """
import json, sys, tempfile
from pathlib import Path
import smara.research_watch as watches
import smara.app_adapter
import smara.research_chat
import smara.research_completeness
from smara.harness import SessionEngine, verification_scope_for_command
from smara.completion_quality import final_answer_reports_unresolved_work
assert final_answer_reports_unresolved_work('The feature was left unimplemented.'), 'Missing completion guard'
assert not final_answer_reports_unresolved_work('Added validation for missing input.'), 'Completion guard false positive'
assert verification_scope_for_command('& "C:/Program Files/Python/python.exe" -m unittest discover') == 'focused', 'Quoted test runner lost its receipt'
assert verification_scope_for_command('pytest || echo passed') == 'none', 'Masked test exit accepted'
assert 'question_sha256' in SessionEngine.finish_incremental.__code__.co_consts, 'Stale completeness receipt guard'
assert getattr(sys, 'frozen', False), 'Executor must be frozen'
assert Path(watches.__file__).is_relative_to(Path(sys._MEIPASS)), 'Watch module came from a checkout'
with tempfile.TemporaryDirectory() as directory:
 store = watches.ResearchWatchStore(directory)
 try:
  store.add('invalid interval', 0.001)
 except ValueError:
  pass
 else:
  raise AssertionError('Sub-hour interval accepted')
 watch = store.add('bundle probe', baseline={'status':'completed', 'research_review':{'claims':[{'claim':'original','supported':True}], 'evidence':[]}})
 for _ in range(25):
  store.run(watch['id'], runner=lambda *_: {'status':'tool_error'})
 assert store._previous_snapshot(watch['id'])['claims'][0]['claim'] == 'original'
 assert len(store.history(watch['id'])) == 20
 store.remove(watch['id'])
 assert not store.get(watch['id'])['enabled']
print(json.dumps({'frozen':True, 'checkout_required':False, 'watch_storage':'passed', 'canonical_adapter_import':'passed', 'completion_guard':'passed', 'test_receipts':'passed'}))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executor", type=Path)
    args = parser.parse_args()
    executable = args.executor.resolve(strict=True)
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "SMARA_REPO_ROOT", "SMARA_DESKTOP_EXECUTABLE"):
        environment.pop(name, None)
    environment["PATH"] = str(Path(environment.get("SystemRoot", "C:/Windows")) / "System32")
    with tempfile.TemporaryDirectory(prefix="smara-bundle-probe-") as directory:
        result = subprocess.run(
            [str(executable), "--python-bridge"], input=PROBE,
            cwd=directory, env=environment, text=True, encoding="utf-8",
            capture_output=True, timeout=120,
        )
    if result.returncode:
        raise SystemExit(f"Frozen executor probe failed: {result.stderr.strip()}")
    print(json.dumps(json.loads(result.stdout), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
