"""Opt-in real-provider coding on isolated committed Smara repository snapshots.

Two tasks reproduce historical bugs; one targets a current path-safety defect.
External probes and original test hashes are not supplied to the model.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from run_coding_acceptance import invoke_cli, failed_receipts

REPO = Path(__file__).resolve().parents[1]
TASKS = [
    ('git_review', '56ebe0c', 'src/smara/git_agent.py',
     'Fix Git workspace review: preserve staged/unstaged status columns including the first record; detect only actual Git unmerged files without recursively scanning unrelated folders or mistaking marker examples for conflicts. Keep public API shapes. Add focused regressions.',
     "from smara.git_agent import GitWorkspaceManager\nimport tempfile,subprocess\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n p=Path(d); g=lambda *a: subprocess.run(['git','-C',d,*a],capture_output=True,check=True)\n g('init','-b','main');g('config','user.name','Acceptance');g('config','user.email','test@example.invalid')\n (p/'one.txt').write_text('original');g('add','one.txt');g('commit','-m','initial');(p/'one.txt').write_text('modified')\n m=GitWorkspaceManager(p);s=m.get_status();assert s.unstaged_files==['one.txt'] and not s.staged_files, s\n (p/'example.md').write_text('<<<<<<< HEAD\\na\\n=======\\nb\\n>>>>>>> branch\\n');assert m.detect_conflicts()==[]\n g('checkout','--','one.txt');g('checkout','-b','other');(p/'one.txt').write_text('other');g('commit','-am','other');g('checkout','main');(p/'one.txt').write_text('main');g('commit','-am','main')\n assert subprocess.run(['git','-C',d,'merge','other'],capture_output=True).returncode!=0\n assert m.detect_conflicts()==[{'file':'one.txt','path':str(p/'one.txt')}],m.detect_conflicts()\n"),
    ('completion', '56ebe0c', 'src/smara/completion_quality.py',
     'Fix the shared completion classifier so explicit needs_input, admitted unimplemented work, and implementations blocked pending missing approved policy cannot be reported as completed. Avoid flagging a documentation example merely mentioning needs_input or completed validation of missing input. Add focused regressions; preserve the API.',
     "from smara.completion_quality import final_answer_reports_unresolved_work as f\nfor x in ['needs_input — please supply the policy.', 'Not implemented — no approved requirements.', 'Implementation blocked pending policy.', 'policy.txt is absent.']:\n assert f(x),x\nfor x in ['Documented the needs_input status.', 'Added validation for missing input.', 'Implemented policy and tests pass.']:\n assert not f(x),x\n"),
    ('conflict_path_safety', 'ae88e61', 'src/smara/git_agent.py',
     'Harden GitWorkspaceManager.resolve_conflict so it cannot read or modify paths outside its workspace, including absolute outside paths, traversal, and symlinks. Return a clear unsuccessful result for denied paths. Preserve valid in-workspace conflict resolution and do not broaden this into an unrelated Git refactor. Add focused regressions.',
     "from smara.git_agent import GitWorkspaceManager\nimport tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n base=Path(d);p=base/'project';p.mkdir();outside=base/'outside.txt';text='<<<<<<< HEAD\\na\\n=======\\nb\\n>>>>>>> branch\\n';outside.write_text(text);m=GitWorkspaceManager(p)\n for name in [str(outside),'../outside.txt']:\n  ok,msg=m.resolve_conflict(name);assert not ok,(name,msg);assert outside.read_text()==text\n inside=p/'inside.txt';inside.write_text(text);ok,msg=m.resolve_conflict('inside.txt');assert ok,msg;assert inside.read_text()=='a\\n'\n try: (p/'link.txt').symlink_to(outside)\n except OSError: print('symlink probe unavailable on this host')\n else:\n  ok,msg=m.resolve_conflict('link.txt');assert not ok,msg;assert outside.read_text()==text\n"),
]


def snapshot(ref, workspace):
    archive = subprocess.run(['git', 'archive', ref], cwd=REPO, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(workspace, filter='data')


def run_probe(workspace, code):
    env = dict(os.environ, PYTHONPATH=str(workspace/'src'))
    result = subprocess.run([sys.executable, '-c', code], cwd=workspace, env=env,
                            capture_output=True, text=True, timeout=40)
    return {'passed':result.returncode==0, 'exit_code':result.returncode,
            'output':(result.stdout+result.stderr)[-4000:]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', action='append', choices=[t[0] for t in TASKS])
    args=parser.parse_args()
    root=args.output.resolve()
    if root.exists(): parser.error('use a new evidence directory')
    root.mkdir(parents=True)
    from smara.harness import BUDGET_PROFILES, Budget
    BUDGET_PROFILES['acceptance']=Budget(180,40,16,240_000,.20)
    rows=[]
    for name,ref,target,request,checks in TASKS:
        if args.task and name not in args.task: continue
        workspace=root/name;workspace.mkdir()
        snapshot(ref,workspace)
        original_files={str(p.relative_to(workspace)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in workspace.rglob('*') if p.is_file()}
        originals={str(p.relative_to(workspace)):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (workspace/'tests').rglob('*.py')}
        before=(workspace/target).read_text(encoding='utf-8')
        baseline=run_probe(workspace,checks)
        prompt=request+f'\nWorkspace: {workspace}. Target: {target}. Work only in this snapshot. Preserve existing tests; add new tests. Run focused tests with {sys.executable}. No package installs, network, commits, or pushes. The evaluator will review the diff.'
        previous=Path.cwd();os.chdir(workspace)
        try: payload,exit_code,elapsed,log=invoke_cli(workspace,prompt,'sarvam_glm')
        finally: os.chdir(previous)
        independent=run_probe(workspace,checks)
        preserved=all((workspace/p).exists() and hashlib.sha256((workspace/p).read_bytes()).hexdigest()==digest for p,digest in originals.items())
        changed_originals=[p for p,digest in original_files.items() if not (workspace/p).exists()
                           or hashlib.sha256((workspace/p).read_bytes()).hexdigest()!=digest]
        import difflib
        diff=''.join(difflib.unified_diff(before.splitlines(True),(workspace/target).read_text(encoding='utf-8').splitlines(True),fromfile='before/'+target,tofile='after/'+target))
        (root/(name+'.diff')).write_text(diff,encoding='utf-8')
        (root/(name+'-cli.log')).write_text(log,encoding='utf-8')
        row={'task':name,'source_commit':subprocess.check_output(['git','rev-parse',ref],cwd=REPO,text=True).strip(),
             'baseline':baseline,'independent':independent,'tests_preserved':preserved,
             'changed_original_files':changed_originals,
             'status':payload.get('status'),'cli_exit':exit_code,'seconds':elapsed,
             'usage':payload.get('usage'),'tool_failures':failed_receipts(payload),
             'correct':independent['passed'] and preserved,'answer':payload.get('answer')}
        row['false_completion']=row['status']=='completed' and not row['correct']
        rows.append(row);(root/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        print(json.dumps({k:row[k] for k in ['task','status','correct','false_completion','seconds']}),flush=True)


if __name__=='__main__': main()
