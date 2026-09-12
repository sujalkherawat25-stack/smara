"""Interactive Prompt Editor with Tab Autocompletion for Smara CLI.

Provides real-time autocompletion for:
- @file and @folder paths in workspace
- @sym: AST code symbols (classes, functions)
- /slash commands with contextual documentation
- Multi-line editing and persistent command history
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


SLASH_COMMANDS: List[Tuple[str, str]] = [
    ("/model", "Switch or view active LLM (grok, sarvam, ollama, openrouter)"),
    ("/graph", "Inspect AST Code Graph & compute blast radius for a symbol"),
    ("/rules", "View or reload workspace coding rules (.smararules / SMARA.md)"),
    ("/mcp", "List connected MCP servers and active external tools"),
    ("/diff", "View working tree diffs of changes made in this session"),
    ("/test", "Run pytest test suite autonomously and diagnose failures"),
    ("/search", "Run live multi-source web search (Tavily/Exa)"),
    ("/pdf", "Compile an executive PDF report into reports/"),
    ("/docx", "Compile an executive Word DOCX report into reports/"),
    ("/workspace", "Show or switch active workspace directory"),
    ("/approval", "Set permission mode (auto or interactive)"),
    ("/clear", "Clear terminal screen and reset conversation context"),
    ("/exit", "Exit Smara CLI"),
]


if PROMPT_TOOLKIT_AVAILABLE:
    class SmaraCompleter(Completer):
        """Dynamic completer resolving @paths, @symbols, and /commands."""

        def __init__(self, workspace_root: Path | str):
            self.workspace_root = Path(workspace_root).resolve()
            self._cached_files: List[str] = []
            self._cached_symbols: List[str] = []
            self._refresh_cache()

        def _refresh_cache(self) -> None:
            """Index workspace file paths and top-level symbols."""
            ignored = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", "dist", "build"}
            files = []
            try:
                for root, dirs, filenames in os.walk(self.workspace_root):
                    dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".pytest-")]
                    rel_root = Path(root).relative_to(self.workspace_root)
                    for f in filenames:
                        if not f.endswith((".pyc", ".pyo")):
                            rel_p = (rel_root / f).as_posix()
                            if rel_p.startswith("./"):
                                rel_p = rel_p[2:]
                            files.append(rel_p)
            except Exception:
                pass
            self._cached_files = sorted(files)[:500]

            # Index symbols from code graph if available
            try:
                from .code_graph import ASTCodeGraph
                cpg = ASTCodeGraph(self.workspace_root)
                cpg.build()
                self._cached_symbols = sorted(list(cpg.symbol_table.keys()))[:300]
            except Exception:
                self._cached_symbols = []

        def get_completions(self, document, complete_event) -> Iterable[Completion]:
            text = document.text_before_cursor
            word = document.get_word_before_cursor(WORD=True)

            # 1. Slash commands at start of prompt
            if text.startswith("/") and " " not in text:
                prefix = text.lower()
                for cmd, desc in SLASH_COMMANDS:
                    if cmd.lower().startswith(prefix):
                        yield Completion(cmd, start_position=-len(text), display=cmd, display_meta=desc)
                return

            # 2. @file and @folder completions
            if "@" in word:
                at_idx = word.rfind("@")
                target = word[at_idx + 1:].lower()
                
                # Check for symbol prefix @sym:
                if target.startswith("sym:"):
                    sym_target = target[4:]
                    for sym in self._cached_symbols:
                        if sym.lower().startswith(sym_target):
                            yield Completion(
                                f"@sym:{sym}",
                                start_position=-len(word[at_idx:]),
                                display=f"⚡ {sym}",
                                display_meta="AST Symbol",
                            )
                    return

                for f in self._cached_files:
                    if target in f.lower():
                        yield Completion(
                            f"@{f}",
                            start_position=-len(word[at_idx:]),
                            display=f"📄 {f}",
                            display_meta="File",
                        )


class SmaraPromptSession:
    """Manages the interactive terminal prompt with history and autocompletion."""

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()
        self._history_file = Path.home() / ".smara" / "cli_history"
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        self.session = None

        if PROMPT_TOOLKIT_AVAILABLE:
            try:
                completer = SmaraCompleter(self.workspace_root)
                history = FileHistory(str(self._history_file))
                style = Style.from_dict({
                    "prompt": "ansicyan bold",
                    "user": "ansipurple bold",
                })
                self.session = PromptSession(
                    history=history,
                    completer=completer,
                    style=style,
                    complete_while_typing=True,
                )
            except Exception:
                self.session = None

    def prompt(self, user_label: str = "you") -> str:
        """Prompt user with autocompletion and line editing."""
        if self.session:
            try:
                prompt_tokens = [
                    ("class:user", f"\n{user_label}"),
                    ("class:prompt", " ❯ "),
                ]
                return self.session.prompt(prompt_tokens).strip()
            except Exception:
                pass

        # Fallback standard input
        sys.stdout.write(f"\n\033[38;5;141m{user_label}\033[0m \033[38;5;80m❯\033[0m ")
        sys.stdout.flush()
        return input().strip()
