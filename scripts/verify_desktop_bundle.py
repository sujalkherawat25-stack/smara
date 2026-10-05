"""Exercise the actual frozen executor without a checkout or Python on PATH."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


PROBE = r"""
import json, sys, tempfile, time
from pathlib import Path
import smara.research_watch as watches
import smara.app_adapter
import smara.research_chat
import smara.research_completeness
import smara.sandbox_image
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from smara.subagent_orchestrator import normalize_worker_model
from smara.harness import SessionEngine, verification_scope_for_command
from smara.completion_quality import final_answer_reports_unresolved_work
from smara.git_agent import GitWorkspaceManager
from smara.autonomous_agent import SmaraAutonomousAgent
from smara.version import __version__
assert __version__ == '0.1.8', 'Stale executor release'
import smara.desktop_executor as desktop_executor
assert 'codex_harness' not in desktop_executor._run_shared_local_agent_turn.__code__.co_names, 'External CLI diversion present'
assert 'deepcopy' in SmaraAutonomousAgent.execute_tool.__code__.co_names, 'Stale approval argument binding'
assert '--diff-filter=U' in repr(GitWorkspaceManager.detect_conflicts.__code__.co_consts), 'Stale recursive Git conflict scanner'
assert final_answer_reports_unresolved_work('The feature was left unimplemented.'), 'Missing completion guard'
assert final_answer_reports_unresolved_work('needs_input — policy.txt is absent.'), 'Missing explicit status guard'
assert final_answer_reports_unresolved_work('Not implemented — no approved policy.'), 'Missing unimplemented status guard'
assert final_answer_reports_unresolved_work('Implementation blocked pending the policy facts.'), 'Missing blocked status guard'
assert not final_answer_reports_unresolved_work('Added validation for missing input.'), 'Completion guard false positive'
assert verification_scope_for_command('& "C:/Program Files/Python/python.exe" -m unittest discover') == 'focused', 'Quoted test runner lost its receipt'
assert verification_scope_for_command('pytest || echo passed') == 'none', 'Masked test exit accepted'
assert 'question_sha256' in SessionEngine.finish_incremental.__code__.co_consts, 'Stale completeness receipt guard'
assert getattr(sys, 'frozen', False), 'Executor must be frozen'
assert 'current_workspace_revision' in SessionEngine.checkpoint.__code__.co_names, 'Stale full-workspace web checkpoint'
with ProcessPoolExecutor(max_workers=1, mp_context=get_context('spawn')) as pool:
 assert pool.submit(normalize_worker_model, 'glm-5.3').result(timeout=45) == 'glm5.3', 'Frozen worker spawn failed'
assert Path(watches.__file__).is_relative_to(Path(sys._MEIPASS)), 'Watch module came from a checkout'
with tempfile.TemporaryDirectory() as directory:
 root = Path(directory)
 workspace = root / 'project'
 workspace.mkdir()
 web_session = SessionEngine(workspace, 'frozen-web-checkpoint')
 try:
  web_session.set('tool_profile', 'research_web')
  web_session.set('research_required', True)
  web_session.begin_incremental('An evidence-required web question')
  web_session.checkpoint([], {'phase':'model'})
  checkpoint = json.loads(web_session.resolve_artifact(web_session.get('continuation_artifact_id')))
  assert checkpoint['workspace_revision'] == 'web-only:no-workspace-files', 'Web checkpoint scanned unrelated local files'
  assert web_session.finish_incremental('completed', 'Unsupported answer')['status'] == 'needs_input', 'Web revision fix bypassed research gates'
 finally:
  web_session.close()
 from smara.persistent_terminal import PersistentTerminalStore
 terminal = PersistentTerminalStore(root / 'terminal-state')
 started = terminal.start([sys.executable, '--python-bridge'], cwd=workspace, max_seconds=30)
 deadline = time.monotonic() + 15
 while time.monotonic() < deadline:
  receipt = next(item for item in json.loads(terminal.metadata_path.read_text()) if item['id'] == started['session_id'])
  if receipt['status'] != 'running': break
  time.sleep(.05)
 assert receipt['status'] == 'failed' and receipt['exit_code'] == 1, 'Frozen terminal lost its real exit receipt'
 from smara.runtime_session import session_store_for_workspace
 from smara.session_protocol import SessionProtocol
 from smara.session_protocol import ProtocolTurn
 assert "UPDATE protocol_items SET status='completed',payload=? WHERE item_id=?" in ProtocolTurn.finish.__code__.co_consts, 'Stale turn completion race guard'
 protocol = SessionProtocol(session_store_for_workspace(workspace))
 assert 'checkpoint_resume' in protocol.dispatch('initialize')['capabilities'], 'Missing execution continuation'
 protocol.dispatch('thread/create', {'thread_id':'frozen-probe'})
 assert protocol.dispatch('approval/list', {'thread_id':'frozen-probe'})['pending_approvals'] == [], 'Missing bounded approval polling'
 turn = protocol.begin('frozen-probe', 'frozen runtime smoke', approval_mode='ask', approval_handler=lambda _: 'deny')
 try:
  turn.execute('file_write', {'path':'denied.txt'}, lambda: (workspace / 'denied.txt').touch())
 except RuntimeError:
  pass
 else:
  raise AssertionError('Frozen approval did not deny the write')
 assert not (workspace / 'denied.txt').exists(), 'Denied action executed in frozen runtime'
 turn.finish('completed', 'probe complete')
 assert protocol.snapshot('frozen-probe')['turns'][0]['status'] == 'completed'
 outside = root / 'outside.txt'
 text = '<<<<<<< HEAD\nprivate\n=======\nother\n>>>>>>> branch\n'
 outside.write_text(text)
 ok, _ = GitWorkspaceManager(workspace).resolve_conflict('../outside.txt')
 assert not ok and outside.read_text() == text, 'Conflict resolver escaped workspace'
 agent = SmaraAutonomousAgent(api_key='probe', toolset='coding', workspace_root=workspace)
 from smara.research_session import CanonicalResearchSession
 assert 'replace_plan' in CanonicalResearchSession.plan.__code__.co_varnames, 'Stale research planner'
 assert '_smara_mandatory' in smara.research_completeness.review_completeness.__code__.co_consts, 'Stale reviewer context protection'
 assert hasattr(smara.research_completeness, 'repair_incomplete_answer'), 'Missing bounded synthesis repair'
 assert hasattr(SmaraAutonomousAgent, '_research_review_token_headroom'), 'Missing final-review budget guard'
 from smara.research import restricted_content_reason
 assert restricted_content_reason('Subscriber exclusive content. Log in for full access.'), 'Missing restricted-source guard'
 assert restricted_content_reason('Log In. Public support policy.') is None, 'Account navigation blocks public sources'
 from smara.research_sources import authority_domain_groups
 assert authority_domain_groups('Red Hat official policy on OpenSSL')[0] == ('redhat.com',), 'Stale named-authority discovery'
 from smara.autonomous_agent import _clock_only_research_node
 assert _clock_only_research_node("Today's date in Asia/Kolkata") and not _clock_only_research_node('Version as of 2024-01-01'), 'Stale clock/evidence separation'
 agent._research.plan('A required fact', [{'id':'fact','question':'A required fact'}, {'id':'workflow','question':'A retrieval action'}])
 agent._research.index.record_failure('https://example.org/missing', 'HTTP 404')
 recovery = agent._research.plan('A required fact', [{'id':'fact','question':'A required fact'}], replace_plan=True)
 assert recovery['removed_node_ids'] == ['workflow'] and recovery['retained_failure_count'] == 1, 'Frozen replan lost failure ledger'
 assert agent._research.graph.nodes['fact'].state == 'unresolved', 'Frozen replan certified an unverified fact'
 for i in range(20):
  agent._record_coding_discovery('file_read', {'path':str(i)}, str(i))
 assert agent._coding_read_only_streak == 1, 'Distinct review reads treated as stalled'
 from smara.long_context import context_excerpt
 from smara.agent_tools import file_read
 long_text = 'x' * 50000 + 'bundle_tail'
 context_excerpt(long_text, workspace)
 archived = next((workspace / '.smara' / 'long-context').glob('*.txt'))
 assert json.loads(file_read(archived, start_char=50000))['content'] == 'bundle_tail', 'Long-context tail lost'
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
print(json.dumps({'frozen':True, 'checkout_required':False, 'web_checkpoint_scope':'passed', 'worker_spawn':'passed', 'terminal_exit_receipt':'passed', 'checkpoint_protocol':'passed', 'watch_storage':'passed', 'canonical_adapter_import':'passed', 'completion_guard':'passed', 'test_receipts':'passed'}))
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
