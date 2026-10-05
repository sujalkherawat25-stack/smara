"""Bounded, real-provider CLI acceptance on isolated synthetic projects.

Fixtures are generated outside source; independent checks stay outside each
agent workspace. No model mocks or expected answers are supplied to the agent.
Run with the repository virtualenv, explicitly opting in with --live.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def cases():
    return [
        ("ports", "def parse_ports(spec):\n    return [int(x) for x in spec.split(',')]\n",
         "Implement parse_ports: blank or whitespace -> []; comma-separated integers or inclusive ranges, sorted and deduplicated; ports 1..65535 only; reject empty elements, malformed/reversed ranges with ValueError.",
         "assert parse_ports('3-5,4,2') == [2,3,4,5]",
         "assert parse_ports('  ') == []; assert parse_ports('1,65535,1-1') == [1,65535]\nfor x in ['0','65536','8-3','1,,2','1,','abc','1-2-3','-1']:\n    try: parse_ports(x)\n    except ValueError: pass\n    else: raise AssertionError(x)"),
        ("csv_export", "def export_rows(rows):\n    return '\\n'.join(','.join(str(v) for v in row) for row in rows)\n",
         "Fix export_rows to return standards-compliant CSV using only the standard library. Preserve commas, quotes and newlines in fields; empty rows list returns empty string. Keep the API.",
         "import csv, io\nassert list(csv.reader(io.StringIO(export_rows([['a,b','x']])))) == [['a,b','x']]",
         "import csv, io\nrows=[['a\\nb','say \"hi\"'],['','é']]; assert list(csv.reader(io.StringIO(export_rows(rows)))) == rows; assert export_rows([]) == ''"),
        ("boolean_config", "def parse_flag(value):\n    return bool(value)\n",
         "Fix parse_flag. Accept bool unchanged and case-insensitive trimmed strings true/yes/1 and false/no/0. All other values, including integers and None, raise ValueError.",
         "assert parse_flag('false') is False; assert parse_flag('yes') is True",
         "assert parse_flag(' NO ') is False; assert parse_flag(True) is True\nfor x in [None,1,0,'maybe','']:\n    try: parse_flag(x)\n    except ValueError: pass\n    else: raise AssertionError(repr(x))"),
        ("safe_paths", "from pathlib import Path\ndef resolve_child(root, name):\n    return Path(root) / name\n",
         "Fix resolve_child to return an absolute resolved Path strictly inside root. Reject absolute names and paths escaping root (including symlinks) with ValueError; root itself is invalid. Nested children may not exist.",
         "import tempfile\nwith tempfile.TemporaryDirectory() as d:\n    assert resolve_child(d,'a/b').is_absolute()",
         "import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n    assert resolve_child(d,'a/b') == (Path(d)/'a/b').resolve()\n    for x in ['..','../escape','.',str(Path(d).resolve())]:\n        try: resolve_child(d,x)\n        except ValueError: pass\n        else: raise AssertionError(x)"),
        ("mutable_defaults", "def append_item(item, items=[]):\n    items.append(item)\n    return items\n",
         "Fix append_item's shared mutable default while preserving the API: omitted items starts fresh each call; an explicit list is mutated and returned as the same object.",
         "assert append_item(1) == [1]; assert append_item(2) == [2]",
         "x=[]; assert append_item('a',x) is x; assert x==['a']; assert append_item(None)==[None]"),
        ("cross_module_refactor", "def total_price(prices):\n    return sum(prices)\n",
         "Refactor duplicated total calculations in app.py and invoice.py into shared money.total_price. Keep app.total_price and invoice.invoice_total public behavior. Empty input -> 0; reject negative prices with ValueError. Do not change test_existing.py.",
         "from invoice import invoice_total\nassert total_price([2,3]) == 5; assert invoice_total([]) == 0",
         "from invoice import invoice_total\nimport money\nassert money.total_price([1.5,2])==3.5\nfor f in [total_price,invoice_total,money.total_price]:\n    try: f([1,-1])\n    except ValueError: pass\n    else: raise AssertionError(f)"),
        ("cli_errors", "def main(argv):\n    print(int(argv[0]) * 2)\n    return 0\n",
         "Fix main(argv), a CLI function returning exit codes: one integer argument prints double and returns 0; missing/extra/malformed arguments print a helpful error to stderr, return 2, and never raise. Accept negative integers.",
         "import contextlib,io\ns=io.StringIO()\nwith contextlib.redirect_stdout(s): assert main(['3']) == 0\nassert s.getvalue().strip()=='6'",
         "import contextlib,io\nfor args in [[],['x'],['1','2']]:\n    s=io.StringIO()\n    with contextlib.redirect_stderr(s): assert main(args)==2\n    assert s.getvalue().strip()\ns=io.StringIO()\nwith contextlib.redirect_stdout(s): assert main(['-2'])==0\nassert s.getvalue().strip()=='-4'"),
        ("dependency_removal", "import pandas as pd\ndef read_names(path):\n    return pd.read_csv(path)['name'].tolist()\n",
         "Remove the unnecessary pandas dependency from read_names and requirements.txt, using stdlib csv. CSV is UTF-8 with optional BOM; return names as strings in order including empty strings; missing name column raises ValueError. Preserve unrelated requirements.",
         "import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n    p=Path(d)/'x.csv'; p.write_text('name,age\\nAda,2\\n',encoding='utf-8'); assert read_names(p)==['Ada']",
         "import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n    p=Path(d)/'x.csv'; p.write_text('\ufeffname,age\\n\"Doe, Jane\",2\\n,3\\n',encoding='utf-8'); assert read_names(p)==['Doe, Jane','']\n    p.write_text('age\\n2\\n',encoding='utf-8')\n    try: read_names(p)\n    except ValueError: pass\n    else: raise AssertionError('missing column')\nassert 'pandas' not in Path('requirements.txt').read_text(); assert 'httpx==0.28.1' in Path('requirements.txt').read_text()"),
        ("add_regression_tests", "def normalize_tag(tag):\n    return tag.strip().lower()\n",
         "normalize_tag already trims and lowercases strings correctly. Add unittest regression tests in test_tags.py for whitespace, mixed case, empty strings, and Unicode. Do not unnecessarily change working production behavior or existing tests; run tests.",
         "assert normalize_tag(' Hello ') == 'hello'",
         "import ast\nfrom pathlib import Path\nassert normalize_tag(' ÄBC ')== 'äbc'; assert normalize_tag('')==''\np=Path('test_tags.py'); assert p.exists(); tree=ast.parse(p.read_text()); assert sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test') for n in ast.walk(tree))>=4"),
        ("resume_missing_policy", "def discount(amount):\n    raise NotImplementedError('approved policy missing')\n",
         "Implement discount(amount) according to policy.txt. Do not invent the missing policy: if absent, clearly request it and leave the task needs_input. Once provided, implement it and run tests. Invalid negative amounts raise ValueError.",
         "try: discount(-1)\nexcept (ValueError,NotImplementedError): pass\nelse: raise AssertionError('negative accepted')",
         "assert discount(0)==0; assert discount(100)==90; assert discount(20)==18\ntry: discount(-1)\nexcept ValueError: pass\nelse: raise AssertionError('negative accepted')"),
    ]


def decode_result(log):
    decoder = json.JSONDecoder()
    for pos, char in enumerate(log):
        if char != '{':
            continue
        try:
            value, _ = decoder.raw_decode(log[pos:])
        except ValueError:
            continue
        if isinstance(value, dict) and 'session_id' in value and 'answer' in value:
            return value
    return {}


def failed_receipts(payload):
    return [{'call_id':r.get('call_id'),'status':r.get('status'),'exit_code':r.get('exit_code'),
             'error_kind':r.get('error_kind')} for r in payload.get('verification',[])
            if isinstance(r,dict) and (r.get('status') not in {'ok','completed'} or r.get('exit_code') not in {None,0})]


def invoke_cli(workspace, prompt, model, resume=None):
    from smara.cli import main
    import smara.task_memory as memory
    memory._default_store = memory.TaskMemoryStore(workspace / '.smara/task-memory')
    prompt_path = workspace.parent / (workspace.name + '-prompt.txt')
    prompt_path.write_text(prompt, encoding='utf-8')
    args = ['--plain', '--model', model, '--workspace', str(workspace)]
    if resume:
        args += ['resume', resume, '--json']
    else:
        args += ['run','--workspace',str(workspace),'--tool-profile','coding',
                 '--budget-profile','acceptance','--prompt-file',str(prompt_path),'--json']
    captured = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            code = main(args)
        except Exception as exc:
            # Logs contain no key values: report only exception type here.
            code = 1
            print(json.dumps({'runner_error': type(exc).__name__}))
    log = captured.getvalue()
    return decode_result(log), code, round(time.monotonic()-started, 2), log


def probe(workspace, code):
    result = subprocess.run([sys.executable,'-c','from app import *\n'+code],
        cwd=workspace,capture_output=True,text=True,timeout=30)
    return {'passed': result.returncode == 0, 'exit_code': result.returncode,
            'output': (result.stdout+result.stderr)[-4000:]}


def run_suite(root, model, selected=None):
    rows = []
    original_cwd = Path.cwd()
    for name, source, request, visible, hidden in cases():
        if selected and name not in selected:
            continue
        workspace = root / name
        workspace.mkdir(parents=True)
        (workspace/'app.py').write_text(source, encoding='utf-8')
        test = 'import unittest\nfrom app import *\nclass Existing(unittest.TestCase):\n    def test_contract(self):\n' + '\n'.join('        '+line for line in visible.splitlines())+'\n'
        (workspace/'test_existing.py').write_text(test,encoding='utf-8')
        if name == 'cross_module_refactor':
            (workspace/'invoice.py').write_text('def invoice_total(prices):\n    return sum(prices)\n',encoding='utf-8')
        if name == 'dependency_removal':
            (workspace/'requirements.txt').write_text('pandas==2.2.3\nhttpx==0.28.1\n',encoding='utf-8')
        baseline = probe(workspace, hidden)
        os.chdir(workspace)
        prompt = request + f"\nWorkspace: {workspace}. Commands run in a Linux workspace container. Use python3 -m unittest discover -v as a standalone command. Preserve test_existing.py. You may edit source and add regression tests. Do not install packages, use the network, or touch files outside this workspace."
        payload, exit_code, elapsed, log = invoke_cli(workspace, prompt, model)
        (root/(name+'-cli.log')).write_text(log,encoding='utf-8')
        initial_status = payload.get('status')
        resume_info = None
        if name == 'resume_missing_policy' and payload.get('session_id') and initial_status == 'needs_input':
            (workspace/'policy.txt').write_text('Approved fictional policy: return amount * 0.9 for nonnegative amounts. Negative amounts raise ValueError.\n',encoding='utf-8')
            # Resume in a fresh OS process; the initial request/session is preserved.
            child = subprocess.run([sys.executable,str(Path(__file__).resolve()),'--resume-child',
                str(workspace),payload['session_id'],model],capture_output=True,text=True,timeout=180)
            (root/(name+'-resume.log')).write_text(child.stdout+child.stderr,encoding='utf-8')
            resume_info = json.loads(child.stdout) if child.returncode == 0 else {'error':'resume process failed'}
            payload = resume_info.get('payload',payload)
            exit_code = resume_info.get('cli_exit',1)
            elapsed += resume_info.get('elapsed_seconds',0)
        verification = subprocess.run([sys.executable,'-m','unittest','discover','-v'],cwd=workspace,capture_output=True,text=True,timeout=30)
        independent = probe(workspace,hidden)
        tests_preserved = (workspace/'test_existing.py').read_text(encoding='utf-8') == test
        correct = independent['passed'] and verification.returncode==0 and tests_preserved
        row = {'task':name,'initial_status':initial_status,'status':payload.get('status'),
            'cli_exit':exit_code,'elapsed_seconds':round(elapsed,2),'usage':payload.get('usage'),
            'tool_failures':failed_receipts(payload),
            'baseline':baseline,'independent':independent,'visible_test_exit':verification.returncode,
            'visible_test_output':verification.stdout+verification.stderr,'tests_preserved':tests_preserved,
            'correct':correct,'false_completion':payload.get('status')=='completed' and not correct,
            'answer':payload.get('answer'),'session_id':payload.get('session_id'),'resume':resume_info,
            'diff':''.join(difflib.unified_diff(source.splitlines(True),(workspace/'app.py').read_text(encoding='utf-8').splitlines(True),fromfile='before/app.py',tofile='after/app.py'))}
        rows.append(row)
        (root/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        os.chdir(original_cwd)
        print(json.dumps({k:row[k] for k in ['task','status','correct','false_completion','elapsed_seconds']}),flush=True)
    return rows


def main():
    from smara.harness import BUDGET_PROFILES, Budget
    BUDGET_PROFILES['acceptance'] = Budget(180,40,16,240_000,.20)
    if len(sys.argv)>1 and sys.argv[1]=='--resume-child':
        workspace=Path(sys.argv[2]); os.chdir(workspace)
        payload,code,elapsed,log=invoke_cli(workspace,'',sys.argv[4],resume=sys.argv[3])
        (workspace.parent/(workspace.name+'-resume-cli.log')).write_text(log,encoding='utf-8')
        print(json.dumps({'payload':payload,'cli_exit':code,'elapsed_seconds':elapsed}))
        return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--model',default='sarvam_glm')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--task',action='append',choices=[c[0] for c in cases()])
    args=parser.parse_args()
    root=args.output.resolve()
    if root.exists():
        parser.error('output must be a new directory to preserve previous evidence')
    from smara.cli import _load_local_profiles,_resolve_profile_key
    profiles,_,credentials=_load_local_profiles()
    profile=next((p for p in profiles if p.get('id')==args.model),None)
    if not profile or not _resolve_profile_key(profile,credentials):
        parser.error('selected real model profile is not configured')
    root.mkdir(parents=True)
    rows=run_suite(root,args.model,args.task)
    print(json.dumps({'tasks':len(rows),'correct':sum(r['correct'] for r in rows),'false_completions':sum(r['false_completion'] for r in rows)}))


if __name__=='__main__':
    main()
