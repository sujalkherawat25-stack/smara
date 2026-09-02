"""Local Code Property Graph (CPG) engine for Smara.

Provides deterministic AST symbol indexing, multi-hop caller-callee traversal,
import dependency DAGs, test coverage association, and blast radius calculation.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger("smara.code_graph")

MAX_GRAPH_SCAN_FILES = 500
MAX_GRAPH_FILE_BYTES = 1024 * 1024  # 1 MB per file limit


@dataclass
class SymbolInfo:
    name: str
    kind: str  # "function", "async_function", "class", "method", "variable"
    file_path: str
    line_number: int
    end_line_number: int
    docstring: str = ""
    parameters: list[str] = field(default_factory=list)
    calls: set[str] = field(default_factory=set)
    called_by: set[str] = field(default_factory=set)


class _ASTSymbolVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.symbols: dict[str, SymbolInfo] = {}
        self.imports: set[str] = set()
        self._current_class: str | None = None
        self._current_function: str | None = None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full_name = f"{module}.{alias.name}" if module else alias.name
            self.imports.add(full_name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        docstring = ast.get_docstring(node) or ""
        symbol = SymbolInfo(
            name=node.name,
            kind="class",
            file_path=self.relative_path,
            line_number=node.lineno,
            end_line_number=getattr(node, "end_lineno", node.lineno),
            docstring=docstring[:500],
        )
        self.symbols[node.name] = symbol
        old_class = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node, is_async=True)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        name = f"{self._current_class}.{node.name}" if self._current_class else node.name
        kind = "method" if self._current_class else ("async_function" if is_async else "function")
        docstring = ast.get_docstring(node) or ""
        params = [arg.arg for arg in getattr(node.args, "posonlyargs", [])]
        params.extend(arg.arg for arg in node.args.args)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        params.extend(arg.arg for arg in node.args.kwonlyargs)
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
        symbol = SymbolInfo(
            name=name,
            kind=kind,
            file_path=self.relative_path,
            line_number=node.lineno,
            end_line_number=getattr(node, "end_lineno", node.lineno),
            docstring=docstring[:500],
            parameters=params,
        )
        self.symbols[name] = symbol
        old_fn = self._current_function
        self._current_function = name
        self.generic_visit(node)
        self._current_function = old_fn

    def visit_Call(self, node: ast.Call) -> None:
        if self._current_function:
            caller = self.symbols.get(self._current_function)
            if caller:
                callee_name = None
                if isinstance(node.func, ast.Name):
                    callee_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    callee_name = node.func.attr
                if callee_name:
                    caller.calls.add(callee_name)
        self.generic_visit(node)


class CodePropertyGraph:
    """In-memory Code Property Graph for workspace repositories."""

    def __init__(self, root_dir: str | Path):
        self.root = Path(root_dir).resolve()
        self.symbols: dict[str, SymbolInfo] = {}  # symbol_name -> SymbolInfo
        self.file_symbols: dict[str, list[str]] = {}  # file_path -> [symbol_name]
        self.file_imports: dict[str, set[str]] = {}  # file_path -> {imported_modules}
        self.file_dependents: dict[str, set[str]] = {}  # module/file -> {files_that_import_it}
        self._file_hashes: dict[str, str] = {}
        self._indexed = False

    def index(self, force: bool = False) -> int:
        """Parse all Python files in the workspace and build the symbol/call DAG."""
        if self._indexed and not force:
            return len(self.symbols)

        scanned = 0
        self.symbols.clear()
        self.file_symbols.clear()
        self.file_imports.clear()
        self.file_dependents.clear()

        for dirpath, dirnames, filenames in os.walk(self.root):
            # Skip hidden, git, cache, and virtual environment directories
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                and d not in {"__pycache__", "node_modules", "venv", ".venv", "dist", "build", "target"}
            ]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                file_path = Path(dirpath) / filename
                try:
                    rel_path = file_path.relative_to(self.root).as_posix()
                    if file_path.stat().st_size > MAX_GRAPH_FILE_BYTES:
                        continue
                    source = file_path.read_text(encoding="utf-8", errors="replace")
                    self._index_file(rel_path, source)
                    scanned += 1
                    if scanned >= MAX_GRAPH_SCAN_FILES:
                        break
                except Exception as exc:
                    LOG.debug("Skipping unparseable file %s: %s", file_path, exc)

        # Build reverse caller & dependency relationships
        self._resolve_graph_edges()
        self._indexed = True
        return len(self.symbols)

    def _index_file(self, rel_path: str, source: str) -> None:
        try:
            tree = ast.parse(source, filename=rel_path)
            visitor = _ASTSymbolVisitor(rel_path)
            visitor.visit(tree)

            self.file_symbols[rel_path] = list(visitor.symbols.keys())
            self.file_imports[rel_path] = visitor.imports
            for symbol_name, symbol_info in visitor.symbols.items():
                self.symbols[symbol_name] = symbol_info
        except SyntaxError:
            pass

    def _resolve_graph_edges(self) -> None:
        # Resolve caller-callee links
        for caller_name, caller_info in self.symbols.items():
            for called_name in caller_info.calls:
                # Direct match or method match
                callee = self.symbols.get(called_name)
                if callee:
                    callee.called_by.add(caller_name)

        # Resolve file-level import dependents
        for file_path, imports in self.file_imports.items():
            module_name = file_path.replace("/", ".").replace(".py", "")
            for imp in imports:
                if imp not in self.file_dependents:
                    self.file_dependents[imp] = set()
                self.file_dependents[imp].add(file_path)

    def inspect_symbol(self, name: str) -> dict[str, Any] | None:
        """Return structural details, signature, docstring, callers, and callees for a symbol."""
        if not self._indexed:
            self.index()

        symbol = self.symbols.get(name)
        if not symbol:
            # Try fuzzy or lowercase search
            needle = name.lower()
            matching = [s for s in self.symbols if s.lower() == needle or s.lower().endswith(f".{needle}")]
            if matching:
                symbol = self.symbols[matching[0]]
        if not symbol:
            return None

        return {
            "name": symbol.name,
            "kind": symbol.kind,
            "file": symbol.file_path,
            "line_number": symbol.line_number,
            "end_line_number": symbol.end_line_number,
            "parameters": symbol.parameters,
            "docstring": symbol.docstring or "(no docstring)",
            "calls": sorted(symbol.calls),
            "called_by": sorted(symbol.called_by),
        }

    def blast_radius(self, target: str) -> dict[str, Any]:
        """Compute the downstream impact (callers, importing files, and tests) of editing a symbol or file."""
        if not self._indexed:
            self.index()

        target_clean = target.replace("\\", "/").strip()
        impacted_files: set[str] = set()
        impacted_symbols: set[str] = set()
        associated_tests: set[str] = set()

        # Check if target is a file or symbol
        is_file = target_clean in self.file_symbols or target_clean.endswith(".py")
        if is_file:
            impacted_files.add(target_clean)
            # Find files that import this module
            mod_name = target_clean.replace("/", ".").replace(".py", "")
            mod_suffix = Path(target_clean).stem
            for imp, callers in self.file_dependents.items():
                if imp == mod_name or imp.endswith(f".{mod_suffix}") or imp == mod_suffix:
                    impacted_files.update(callers)
            # Find symbols in this file
            file_syms = self.file_symbols.get(target_clean, [])
            for sym in file_syms:
                symbol_info = self.symbols.get(sym)
                if symbol_info:
                    impacted_symbols.update(symbol_info.called_by)
        else:
            # Target is a symbol name
            symbol = self.symbols.get(target_clean)
            if symbol:
                impacted_files.add(symbol.file_path)
                impacted_symbols.update(symbol.called_by)
                for caller in symbol.called_by:
                    caller_info = self.symbols.get(caller)
                    if caller_info:
                        impacted_files.add(caller_info.file_path)

        # Classify affected test suites
        for f in impacted_files:
            if "test" in f.lower():
                associated_tests.add(f)
            else:
                # Find matching test file if exists
                test_candidate = f"tests/test_{Path(f).name}"
                if test_candidate in self.file_symbols:
                    associated_tests.add(test_candidate)

        return {
            "target": target,
            "is_file": is_file,
            "impacted_files_count": len(impacted_files),
            "impacted_files": sorted(impacted_files)[:50],
            "impacted_callers": sorted(impacted_symbols)[:50],
            "associated_tests": sorted(associated_tests),
        }

    def find_references(self, symbol_name: str) -> list[dict[str, Any]]:
        """Return all occurrences and call references across the workspace."""
        if not self._indexed:
            self.index()

        references: list[dict[str, Any]] = []
        symbol = self.symbols.get(symbol_name)
        if symbol:
            references.append({
                "file": symbol.file_path,
                "line": symbol.line_number,
                "kind": "definition",
                "symbol": symbol.name,
            })
            for caller in sorted(symbol.called_by):
                caller_info = self.symbols.get(caller)
                if caller_info:
                    references.append({
                        "file": caller_info.file_path,
                        "line": caller_info.line_number,
                        "kind": "call",
                        "caller": caller_info.name,
                    })
        return references
