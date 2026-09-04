"""Claude Code inspired Terminal UI (TUI) renderer for Smara CLI."""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Any, Iterable


class Colors:
    """ANSI color codes with 256-color palette and styling."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    # Claude Code palette
    ANTHROPIC_ORANGE = "\033[38;5;208m"
    BRAND_CORAL = "\033[38;5;209m"
    CYAN = "\033[38;5;80m"
    BLUE = "\033[38;5;75m"
    PURPLE = "\033[38;5;141m"
    GREEN = "\033[38;5;114m"
    YELLOW = "\033[38;5;221m"
    RED = "\033[38;5;203m"
    GRAY = "\033[38;5;245m"
    DARK_GRAY = "\033[38;5;238m"
    WHITE = "\033[38;5;255m"
    BG_DARK = "\033[48;5;235m"


class Glyphs:
    """Unicode / ASCII fallback glyphs."""
    def __init__(self, utf8: bool = True):
        self.brand = "✦" if utf8 else ">"
        self.bullet = "•" if utf8 else "-"
        self.online = "*" if utf8 else "*"
        self.offline = "o" if utf8 else "o"
        self.prompt = "❯" if utf8 else ">"
        self.arrow = "→" if utf8 else "->"
        self.tool = "⚙" if utf8 else "[tool]"
        self.graph = "⚡" if utf8 else "[graph]"
        self.search = "🔍" if utf8 else "[search]"
        self.doc = "📄" if utf8 else "[doc]"
        self.terminal = "❯_" if utf8 else "[sh]"
        self.ok = "✓" if utf8 else "OK"
        self.fail = "✗" if utf8 else "ERR"
        self.spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"] if utf8 else ["|", "/", "-", "\\"]
        self.corner = "└─" if utf8 else "+-"
        self.pipe = "│" if utf8 else "|"
        self.dash = "─" if utf8 else "-"


class TerminalRenderer:
    """Fast, dependency-free Claude Code inspired terminal renderer."""

    def __init__(self, *, plain: bool = False):
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        self.plain = plain or not sys.stdout.isatty()
        encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
        self.utf8 = "utf" in encoding or os.name != "nt"
        self.glyphs = Glyphs(utf8=self.utf8)

    def paint(self, text: str, color: str) -> str:
        if self.plain:
            return text
        code = getattr(Colors, color.upper(), "")
        return f"{code}{text}{Colors.RESET}"

    def print_banner(self, *, model_label: str, workspace_path: str, zero_friction: bool = True) -> None:
        if self.plain:
            print(f"Smara Autonomous CLI | Model: {model_label} | Workspace: {workspace_path}")
            return

        print()
        brand_badge = self.paint(f" {self.glyphs.brand} SMARA ", "BOLD")
        version_badge = self.paint(" v2.0 Autonomous ", "CYAN")
        print(f" {brand_badge}{version_badge}")
        
        status_line = (
            f"  {self.paint('Model:', 'GRAY')} {self.paint(model_label, 'WHITE')}  "
            f"{self.paint(self.glyphs.bullet, 'DARK_GRAY')}  "
            f"{self.paint('Workspace:', 'GRAY')} {self.paint(workspace_path, 'CYAN')}  "
            f"{self.paint(self.glyphs.bullet, 'DARK_GRAY')}  "
            f"{self.paint('Safety:', 'GRAY')} {self.paint('⚡ 0 Approvals', 'GREEN') if zero_friction else self.paint('Ask', 'YELLOW')}"
        )
        print(status_line)
        print(self.paint("  Type /help for slash commands | Ctrl+C to cancel | Ctrl+D to exit", "GRAY"))
        print()

    def print_prompt(self, user_name: str = "you") -> None:
        tag = self.paint(f"\n{user_name}", "PURPLE")
        prompt = self.paint(f" {self.glyphs.prompt} ", "CYAN")
        sys.stdout.write(f"{tag}{prompt}")
        sys.stdout.flush()

    def print_assistant_header(self, model_name: str = "Smara") -> None:
        header = f"\n{self.paint(self.glyphs.brand, 'BRAND_CORAL')} {self.paint(model_name, 'BOLD')}\n"
        sys.stdout.write(header)
        sys.stdout.flush()

    def stream_markdown_chunk(self, chunk: str) -> None:
        """Stream token chunks directly to stdout."""
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def print_thought(self, thought: str) -> None:
        glyph = "🧠" if self.utf8 else "[thought]"
        prefix = self.paint(f"  {glyph} Thinking: ", "PURPLE")
        text = self.paint(thought, "GRAY")
        print(f"{prefix}{text}")

    def print_progress(self, message: str) -> None:
        glyph = "⚡" if self.utf8 else ">"
        prefix = self.paint(f"  {glyph} Executing: ", "CYAN")
        text = self.paint(message, "WHITE")
        print(f"{prefix}{text}")

    def print_tool_start(self, capability: str, title: str) -> None:
        icon = self.glyphs.tool
        if "graph" in capability:
            icon = self.glyphs.graph
        elif "integration" in capability or "search" in capability:
            icon = self.glyphs.search
        elif "write" in capability or "doc" in capability:
            icon = self.glyphs.doc
        elif "read" in capability or "file" in capability:
            icon = "📖" if self.utf8 else "[read]"
        elif "research" in capability or "market" in capability:
            icon = "📊" if self.utf8 else "[research]"
        elif "terminal" in capability:
            icon = self.glyphs.terminal

        prefix = self.paint(f"  {self.glyphs.corner} {icon} ", "YELLOW")
        action = self.paint(capability, "BOLD")
        detail = self.paint(f" ({title})", "GRAY")
        print(f"{prefix}{action}{detail}")

    def print_tool_result(self, capability: str, ok: bool, summary: str = "") -> None:
        icon = self.paint(f"    {self.glyphs.ok} ", "GREEN") if ok else self.paint(f"    {self.glyphs.fail} ", "RED")
        status_color = "GREEN" if ok else "RED"
        msg = self.paint(summary or ("Completed" if ok else "Failed"), status_color)
        print(f"{icon}{msg}")

    def print_stats(self, duration_sec: float, tools_count: int = 0) -> None:
        parts = [f"{duration_sec:.2f}s"]
        if tools_count > 0:
            parts.append(f"{tools_count} tool{'s' if tools_count != 1 else ''}")
        stats = self.paint(f" ({', '.join(parts)})", "DARK_GRAY")
        print(f"{stats}\n")

    def print_error(self, message: str) -> None:
        icon = self.paint(f"\n{self.glyphs.fail} Error:", "RED")
        print(f"{icon} {message}", file=sys.stderr)

    def print_help(self) -> None:
        print(self.paint("\nSmara Autonomous CLI — Slash Commands", "BOLD"))
        commands = [
            ("/model [NAME]", "Switch or view active LLM (grok, sarvam, ollama, openrouter)"),
            ("/graph <SYMBOL>", "Inspect AST Code Graph & compute blast radius"),
            ("/search <QUERY>", "Run live multi-source web search (Tavily/Exa)"),
            ("/pdf <TITLE>", "Compile an executive PDF report into reports/"),
            ("/docx <TITLE>", "Compile an executive Word DOCX report into reports/"),
            ("/test [FILTER]", "Run pytest test suite autonomously and summarize"),
            ("/workspace [PATH]", "Show or switch active workspace directory"),
            ("/history", "View recent turns in this session"),
            ("/clear", "Clear terminal screen and reset conversation context"),
            ("/exit", "Exit Smara CLI"),
        ]
        for cmd, desc in commands:
            print(f"  {self.paint(cmd.ljust(22), 'CYAN')} {self.paint(desc, 'GRAY')}")
        print()
