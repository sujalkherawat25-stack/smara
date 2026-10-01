"""Opt-in real CLI checks using only synthetic long-message data."""
import argparse
import json
from pathlib import Path

from run_coding_acceptance import invoke_cli


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists():
        parser.error('Use a fresh evidence directory')
    root.mkdir(parents=True)
    from smara.harness import BUDGET_PROFILES, Budget
    BUDGET_PROFILES['acceptance'] = Budget(150, 20, 10, 240000, .20)
    rows = []
    for name, size in [('inline_tail', 24000), ('archived_middle', 160000)]:
        workspace = root / name
        workspace.mkdir()
        filler = 'Synthetic neutral filler line.\n'
        text = (filler * (size // len(filler)))
        midpoint = len(text) // 2
        text = text[:midpoint] + '\nMIDDLE_CODE=violet-731\n' + text[midpoint:] + '\nTAIL_CODE=amber-942\n'
        prompt = ('This is a synthetic read-only text extraction task. Do not modify files or run tests. '
                  'Use the CLI final format: FINAL ANSWER: followed by the MIDDLE_CODE and TAIL_CODE values separated by a comma. '
                  'If the text is archived, read or search its stored file; do not guess.\n\n' + text)
        result, exit_code, elapsed, log = invoke_cli(workspace, prompt, 'sarvam_glm')
        answer = str(result.get('answer') or '')
        row = {'task': name, 'input_chars': len(prompt), 'status': result.get('status'),
               'exit_code': exit_code, 'seconds': elapsed, 'usage': result.get('usage'),
               'correct': 'violet-731' in answer and 'amber-942' in answer, 'answer': answer}
        rows.append(row)
        (root / f'{name}-cli.log').write_text(log, encoding='utf-8')
        (root / 'results.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
