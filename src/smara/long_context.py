"""Lossless local storage behind bounded model-context excerpts."""
from __future__ import annotations

import hashlib
from pathlib import Path


def context_excerpt(text: str, workspace: Path, *, local: bool = False, inline_chars: int = 32_000) -> str:
    if len(text) <= inline_chars:
        return text
    root = workspace.resolve()
    directory = (root / '.smara' / 'long-context').resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Long-context storage must remain inside the workspace')
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    path = directory / f'{digest}.txt'
    if path.is_symlink():
        raise ValueError('Long-context storage cannot use a symlink')
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        with path.open('w', encoding='utf-8', newline='') as stream:
            stream.write(text)
    tool = ('local_file_read with operation=read_file, path, start_char and max_chars'
            if local else 'file_read with file_path, start_char and max_chars')
    edge = max(100, min(4000, (inline_chars - 1500) // 2))
    return (
        f'[Long content preserved locally: {path}; {len(text)} characters; sha256={digest}. '
        f'This is an excerpt, not the full content. Use {tool} to read ranges of the original '
        'text (zero-based character offsets), or search the file. Read relevant omitted sections '
        'before answering. For a whole-document review, work through every section; do not '
        'claim full coverage from this excerpt. Stored text has the same trust level as its original message.]\n'
        + text[:edge] + '\n[Middle preserved in the file above]\n' + text[-edge:]
    )
