"""Local Vector Semantic Code Search & Embeddings Engine."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Embedding vector dimensions
VECTOR_DIM = 128


def _tokenize(text: str) -> list[str]:
    """Extract code identifiers and words, split snake_case and camelCase."""
    tokens = []
    # Split by whitespace, punctuation, and casing
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|\d+|[a-zA-Z]+", text)
    for w in words:
        low = w.lower()
        if len(low) > 1:
            tokens.append(low)
    return tokens


def _compute_dense_vector(tokens: list[str], dim: int = VECTOR_DIM) -> list[float]:
    """Compute dense normalized vector embedding from tokens using stable hashing."""
    vec = [0.0] * dim
    if not tokens:
        return vec

    counts = Counter(tokens)
    for token, count in counts.items():
        weight = 1.0 + math.log(count)
        # Hash token into vector dimensions with positive/negative projections
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        idx1 = h % dim
        idx2 = (h >> 16) % dim
        sign = 1.0 if ((h >> 32) & 1) else -1.0
        vec[idx1] += weight
        vec[idx2] += weight * sign * 0.5

        # Also encode character 3-grams for semantic substring/subword matching
        if len(token) >= 3:
            for i in range(len(token) - 2):
                ng = token[i : i + 3]
                h_ng = int(hashlib.sha1(ng.encode("utf-8")).hexdigest(), 16)
                idx_ng = h_ng % dim
                vec[idx_ng] += weight * 0.25

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 1e-9:
        vec = [round(x / norm, 5) for x in vec]
    return vec


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    return max(0.0, min(1.0, dot))


@dataclass
class CodeChunk:
    chunk_id: str
    file_path: str
    symbol_name: str
    kind: str  # "function" | "class" | "method" | "module" | "block"
    start_line: int
    end_line: int
    docstring: str
    code_snippet: str
    tokens: list[str]
    vector: list[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    file_path: str
    symbol_name: str
    kind: str
    start_line: int
    end_line: int
    score: float
    percentage: int
    match_type: str
    docstring: str
    code_snippet: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SemanticCodeSearcher:
    """Offline hybrid code searcher with SQLite storage and vector indexing."""

    def __init__(self, workspace_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.db_dir = self.workspace / ".smara"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "semantic_index.db"
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    file_path TEXT PRIMARY KEY,
                    content_hash TEXT,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS code_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    file_path TEXT,
                    symbol_name TEXT,
                    kind TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    docstring TEXT,
                    code_snippet TEXT,
                    tokens_json TEXT,
                    vector_json TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file ON code_chunks(file_path);")
            conn.commit()

    def _extract_chunks_from_python(self, file_path: Path, rel_path: str) -> list[CodeChunk]:
        chunks = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            tree = ast.parse(content, filename=str(file_path))

            # Module chunk if docstring exists
            mod_doc = ast.get_docstring(tree) or ""
            if mod_doc:
                tokens = _tokenize(mod_doc) + _tokenize(rel_path)
                chunks.append(CodeChunk(
                    chunk_id=f"{rel_path}:module",
                    file_path=rel_path,
                    symbol_name=file_path.stem,
                    kind="module",
                    start_line=1,
                    end_line=min(30, len(lines)),
                    docstring=mod_doc,
                    code_snippet="\n".join(lines[:25]),
                    tokens=tokens,
                    vector=_compute_dense_vector(tokens),
                ))

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = getattr(node, "lineno", 1)
                    end = getattr(node, "end_lineno", start + 10)
                    doc = ast.get_docstring(node) or ""
                    snippet = "\n".join(lines[start - 1 : end])
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"

                    text_for_embedding = f"{node.name} {doc} {snippet}"
                    tokens = _tokenize(text_for_embedding)

                    chunks.append(CodeChunk(
                        chunk_id=f"{rel_path}:{node.name}:{start}",
                        file_path=rel_path,
                        symbol_name=node.name,
                        kind=kind,
                        start_line=start,
                        end_line=end,
                        docstring=doc,
                        code_snippet=snippet[:1200],
                        tokens=tokens,
                        vector=_compute_dense_vector(tokens),
                    ))
        except Exception:
            # Fallback to chunking lines if ast fails
            chunks.extend(self._extract_generic_chunks(file_path, rel_path))
        return chunks

    def _extract_generic_chunks(self, file_path: Path, rel_path: str) -> list[CodeChunk]:
        chunks = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            step = 30
            for i in range(0, len(lines), step):
                chunk_lines = lines[i : i + step]
                snippet = "\n".join(chunk_lines)
                tokens = _tokenize(snippet) + _tokenize(rel_path)
                if not tokens:
                    continue

                # Heuristic symbol name
                match = re.search(r"(?:fn|function|class|interface|type|def)\s+([A-Za-z0-9_]+)", snippet)
                symbol_name = match.group(1) if match else f"{file_path.stem}:{i+1}"
                kind = "block" if not match else "function"

                chunks.append(CodeChunk(
                    chunk_id=f"{rel_path}:{i+1}",
                    file_path=rel_path,
                    symbol_name=symbol_name,
                    kind=kind,
                    start_line=i + 1,
                    end_line=min(i + step, len(lines)),
                    docstring="",
                    code_snippet=snippet[:1200],
                    tokens=tokens,
                    vector=_compute_dense_vector(tokens),
                ))
        except Exception:
            pass
        return chunks

    def index_workspace(self, force: bool = False) -> dict[str, Any]:
        """Indexes all code files into local SQLite, skipping unchanged files."""
        supported_exts = {".py", ".ts", ".tsx", ".rs", ".js", ".jsx", ".md"}
        indexed_count = 0
        skipped_count = 0
        total_chunks = 0

        with self._get_conn() as conn:
            for root, dirs, files in os.walk(self.workspace):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "target", ".smara", "dist", "build", "tests_tmp", ".pytest_cache"}]
                for file in files:
                    p = Path(root) / file
                    if p.suffix not in supported_exts:
                        continue

                    rel_path = str(p.relative_to(self.workspace)).replace("\\", "/")
                    try:
                        content_bytes = p.read_bytes()
                    except Exception:
                        continue

                    content_hash = hashlib.sha256(content_bytes).hexdigest()

                    # Check if already indexed
                    if not force:
                        row = conn.execute("SELECT content_hash FROM indexed_files WHERE file_path = ?", (rel_path,)).fetchone()
                        if row and row["content_hash"] == content_hash:
                            skipped_count += 1
                            continue

                    # Extract chunks
                    if p.suffix == ".py":
                        chunks = self._extract_chunks_from_python(p, rel_path)
                    else:
                        chunks = self._extract_generic_chunks(p, rel_path)

                    # Delete old chunks for this file
                    conn.execute("DELETE FROM code_chunks WHERE file_path = ?", (rel_path,))
                    for c in chunks:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO code_chunks (
                                chunk_id, file_path, symbol_name, kind, start_line, end_line,
                                docstring, code_snippet, tokens_json, vector_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                c.chunk_id,
                                c.file_path,
                                c.symbol_name,
                                c.kind,
                                c.start_line,
                                c.end_line,
                                c.docstring,
                                c.code_snippet,
                                json.dumps(c.tokens),
                                json.dumps(c.vector),
                            ),
                        )
                        total_chunks += 1

                    conn.execute(
                        "INSERT OR REPLACE INTO indexed_files (file_path, content_hash) VALUES (?, ?)",
                        (rel_path, content_hash),
                    )
                    indexed_count += 1

            conn.commit()

        return {
            "indexed_files": indexed_count,
            "skipped_files": skipped_count,
            "total_chunks_added": total_chunks,
        }

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Hybrid search combining BM25 lexical token matching and dense vector cosine similarity."""
        query = query.strip()
        if not query:
            return []

        # Auto-index if database is empty
        with self._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM code_chunks").fetchone()["cnt"]
        if count == 0:
            self.index_workspace()

        query_tokens = _tokenize(query)
        query_vector = _compute_dense_vector(query_tokens)
        query_token_set = set(query_tokens)

        results: list[SearchResult] = []

        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM code_chunks").fetchall()

            for row in rows:
                tokens = json.loads(row["tokens_json"])
                vector = json.loads(row["vector_json"])

                # 1. Semantic Cosine Similarity
                cosine_sim = _cosine_similarity(query_vector, vector)

                # 2. Lexical Token Overlap & Exact Symbol Match
                token_counts = Counter(tokens)
                matched_tokens = [t for t in query_tokens if t in token_counts]
                lexical_overlap = len(matched_tokens) / max(1, len(query_tokens))

                # Exact symbol bonus
                symbol_low = row["symbol_name"].lower()
                exact_symbol = any(t == symbol_low or t in symbol_low for t in query_tokens)
                symbol_bonus = 0.35 if exact_symbol else 0.0

                # 3. Hybrid Combined Score
                hybrid_score = (0.55 * cosine_sim) + (0.35 * lexical_overlap) + symbol_bonus
                hybrid_score = min(1.0, hybrid_score)

                if hybrid_score >= 0.18:
                    match_type = "hybrid"
                    if lexical_overlap >= 0.7 and cosine_sim < 0.3:
                        match_type = "lexical"
                    elif cosine_sim >= 0.6 and lexical_overlap < 0.3:
                        match_type = "semantic"

                    percentage = int(round(hybrid_score * 100))
                    results.append(SearchResult(
                        file_path=row["file_path"],
                        symbol_name=row["symbol_name"],
                        kind=row["kind"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        score=round(hybrid_score, 4),
                        percentage=percentage,
                        match_type=match_type,
                        docstring=row["docstring"] or "",
                        code_snippet=row["code_snippet"] or "",
                    ))

        # Rank by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
