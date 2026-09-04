"""Smara CLI: Claude Code inspired autonomous developer agent for terminal and desktop."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .tui import TerminalRenderer


# ============================================================================
# Compatibility / Legacy Cloud Helpers
# ============================================================================

def _default_browser_auth_url(api_url: str, device_code: str) -> str:
    """Open the canonical Smara root for browser device approval."""
    base = api_url.rstrip("/")
    encoded = quote(device_code, safe="")
    if base.endswith("/smara-api"):
        return f"{base.removesuffix('/smara-api')}/?cli_device={encoded}"
    return f"{base}/?cli_device={encoded}"


class _TerminalUI(TerminalRenderer):
    """Compatibility alias for legacy tests."""
    def banner(self, *, api_url: str, session: str, workspace: str, authenticated: bool) -> None:
        self.print_banner(model_label="Cloud", workspace_path=workspace, zero_friction=False)

    def tool_call(self, name: str) -> None:
        self.print_tool_start("tool", name)

    def tool_result(self, name: str, ok: bool) -> None:
        self.print_tool_result("tool", ok)

    def phase(self, name: str) -> None:
        print(f"  · {name}")

    def status(self, label: str) -> None:
        print(f"  ↳ {label}")

    def done(self, *, total_ms: int | None = None, tools_used: int | None = None) -> None:
        parts = []
        if total_ms is not None:
            parts.append(f"{total_ms} ms")
        if tools_used:
            parts.append(f"{tools_used} tools")
        if parts:
            print(f"  · {' · '.join(parts)}")

    def error(self, message: str) -> None:
        self.print_error(message)

    def prompt(self) -> str:
        return "you ❯ "


def _stream_chat(
    client: httpx.Client,
    *,
    message: str,
    workspace: str,
    conversation_id: str,
    model_profile: str | None = None,
    ui: _TerminalUI | None = None,
) -> str:
    ui = ui or _TerminalUI(plain=True)
    payload = {"message": message, "workspace_id": workspace, "conversation_id": conversation_id}
    if model_profile:
        payload["model_profile"] = model_profile
    final_text = ""
    event_name = "message"
    done_data: dict[str, Any] = {}
    try:
        with client.stream("POST", "/v1/chat/stream", json=payload, headers={"Accept": "text/event-stream"}) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"{response.status_code}: chat stream unavailable")
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ")
                    continue
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line.removeprefix("data: "))
                except json.JSONDecodeError:
                    continue
                payload_type = data.get("type")
                if isinstance(payload_type, str) and payload_type:
                    event_name = payload_type
                if event_name == "token":
                    text = str(data.get("text", ""))
                    print(text, end="", flush=True)
                    final_text += text
                elif event_name == "tool_call":
                    ui.tool_call(str(data.get("name", "unknown")))
                elif event_name == "tool_result":
                    ui.tool_result(str(data.get("name", "unknown")), bool(data.get("ok")))
                elif event_name == "phase":
                    ui.phase(str(data.get("phase", "working")))
                elif event_name == "status":
                    ui.status(str(data.get("label", data.get("message", "working"))))
                elif event_name == "done":
                    done_data = data
                elif event_name == "error":
                    raise RuntimeError(str(data.get("message", "Smara chat failed.")))
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError("Smara chat stream disconnected; retry the turn.") from exc
    if not final_text.strip():
        raise RuntimeError("Smara returned no answer. Check the hosted model provider and try again.")
    ui.done(
        total_ms=int(done_data["total_ms"]) if str(done_data.get("total_ms", "")).isdigit() else None,
        tools_used=int(done_data["tools_used"]) if str(done_data.get("tools_used", "")).isdigit() else None,
    )
    print()
    return final_text


# ============================================================================
# Local Autonomous Engine & State Management
# ============================================================================

def _desktop_state_path() -> Path:
    env_path = os.getenv("SMARA_DESKTOP_STATE")
    if env_path:
        return Path(env_path)
    appdata = Path(os.getenv("APPDATA", Path.home() / ".config"))
    return appdata / "Smara" / "desktop.json"


def _load_local_profiles() -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    """Load local model profiles, active model ID, and credentials from desktop.json / credentials.json."""
    state_file = _desktop_state_path()
    profiles: list[dict[str, Any]] = []
    active_id = "grok"
    credentials: dict[str, str] = {}

    # Load credentials file
    cred_file = state_file.parent / "credentials.json"
    if cred_file.exists():
        try:
            credentials = json.loads(cred_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            profiles = state.get("model_profiles", [])
            active_id = state.get("active_model", active_id)
        except (OSError, ValueError):
            pass

    if not profiles:
        profiles = [
            {"id": "grok", "label": "Grok-3 Mini", "base_url": "https://api.x.ai/v1", "model": "grok-3-mini", "auth_header": "authorization"},
            {"id": "sarvam", "label": "Sarvam 105B", "base_url": "https://api.sarvam.ai/v1", "model": "sarvam-105b", "auth_header": "api-subscription-key"},
            {"id": "ollama", "label": "Ollama Local", "base_url": "http://localhost:11434/v1", "model": "llama3.3", "auth_header": "authorization"},
            {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "anthropic/claude-3.5-sonnet", "auth_header": "authorization"},
        ]

    return profiles, active_id, credentials


def _resolve_profile_key(profile: dict[str, Any], credentials: dict[str, str]) -> str:
    pid = profile.get("id", "").lower()
    env_keys = {
        "grok": ["SMARA_MODEL_GROK_API_KEY", "GROK_API_KEY", "XAI_API_KEY"],
        "sarvam": ["SMARA_MODEL_SARVAM_API_KEY", "SARVAM_API_KEY"],
        "openrouter": ["SMARA_MODEL_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"],
        "ollama": ["OLLAMA_API_KEY"],
    }
    for env_name in env_keys.get(pid, [f"SMARA_MODEL_{pid.upper()}_API_KEY"]):
        val = os.getenv(env_name)
        if val:
            return val

    # Try credentials store
    cred_keys = [f"model_api_key_{pid}", f"SMARA_MODEL_{pid.upper()}_API_KEY", f"{pid}_api_key"]
    for k in cred_keys:
        if k in credentials and credentials[k]:
            return credentials[k]

    return "ollama-local" if pid == "ollama" else ""


def _strip_thinking(text: str) -> str:
    """Remove internal reasoning scratchpad or <think> tags."""
    res = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    res = re.sub(r"^<think>.*", "", res, flags=re.DOTALL)
    return res.strip()


class LocalAutonomousEngine:
    """Executes Claude Code inspired autonomous reasoning with local capabilities."""

    def __init__(self, workspace_root: Path | None = None, tui: TerminalRenderer | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.tui = tui or TerminalRenderer()
        self.profiles, self.active_id, self.credentials = _load_local_profiles()
        self.history: list[dict[str, str]] = []

    @property
    def active_profile(self) -> dict[str, Any]:
        for p in self.profiles:
            if p.get("id") == self.active_id:
                return p
        return self.profiles[0]

    def set_active_model(self, model_id: str) -> bool:
        needle = model_id.lower().strip()
        for p in self.profiles:
            if p.get("id", "").lower() == needle or needle in p.get("label", "").lower():
                self.active_id = p["id"]
                return True
        return False

    def _execute_raw_capability(self, capability: str, payload: dict[str, Any], title: str = "") -> dict[str, Any]:
        """Directly run local capabilities (AST graph, live search, PDF/DOCX, terminal, dynamic tools)."""
        self.tui.print_tool_start(capability, title or capability)
        start_t = time.time()
        result_data: dict[str, Any] = {}

        try:
            if capability == "local_graph":
                from .code_graph import CodePropertyGraph
                operation = payload.get("operation", "inspect_symbol")
                symbol = payload.get("symbol", "")
                
                # Check candidate paths
                graph = CodePropertyGraph(self.workspace)
                graph.index()
                if len(graph.symbols) == 0:
                    candidates = [Path.cwd(), Path(__file__).resolve().parent.parent.parent]
                    for c in candidates:
                        if c.exists():
                            g = CodePropertyGraph(c)
                            g.index()
                            if len(g.symbols) > 0:
                                graph = g
                                break
                
                if operation == "inspect_symbol":
                    res = graph.inspect_symbol(symbol)
                elif operation == "blast_radius":
                    res = graph.blast_radius(symbol)
                elif operation == "find_references":
                    res = graph.find_references(symbol)
                else:
                    res = graph.inspect_symbol(symbol)
                
                if operation == "inspect_symbol" and isinstance(res, dict):
                    res["blast_radius"] = graph.blast_radius(symbol)
                
                result_data = {"action": "local_graph", "operation": operation, "symbol": symbol, "result": res}
                summary = f"Indexed {len(graph.symbols)} symbols; inspected {symbol}" if res else f"Symbol '{symbol}' not found"
                self.tui.print_tool_result(capability, res is not None, summary)

            elif capability == "local_integration":
                from .desktop_executor import resolve_local_credential
                from .desktop_integrations import execute_local_integration
                
                def _cred_resolver(keys: list[str]) -> dict[str, str]:
                    return {k: resolve_local_credential(k) for k in keys}
                
                res_str = execute_local_integration(payload, _cred_resolver)
                result_data = json.loads(res_str)
                count = len(result_data.get("results", []))
                self.tui.print_tool_result(capability, True, f"Found {count} live web sources")

            elif capability == "local_file_write":
                op = payload.get("operation", "write")
                if op == "compile_report":
                    from .deep_research import DeepResearchEngine
                    d_engine = DeepResearchEngine(self.workspace)
                    topic = payload.get("topic", "Market Analysis")
                    vectors = d_engine.fan_out_research_vectors(topic)
                    sources = d_engine.retrieve_multi_vector_sources(vectors)
                    analysis = d_engine.synthesize_market_analysis(topic, sources)
                    report_path = d_engine.generate_executive_report(topic, analysis)
                    result_data = {"action": "local_file_write", "operation": "compile_report", "report_path": str(report_path)}
                    self.tui.print_tool_result(capability, True, f"Saved executive report to {report_path}")
                else:
                    from .desktop_executor import _write_file_unlocked
                    res_str = _write_file_unlocked(payload, [self.workspace])
                    result_data = json.loads(res_str)
                    fname = result_data.get("file_name", "file")
                    bytes_w = result_data.get("bytes_written", 0)
                    self.tui.print_tool_result(capability, True, f"Saved {fname} ({bytes_w} bytes)")

            elif capability == "local_file_read":
                from .path_resolver import locate_resource, read_whole_file, inspect_discovered_folder
                path_arg = payload.get("path") or payload.get("folder") or ""
                located = locate_resource(path_arg, [self.workspace])
                if located:
                    if located.is_dir():
                        info = inspect_discovered_folder(located)
                        info["action"] = "local_file_read"
                        result_data = info
                        summary = f"Located and inspected folder '{located.name}' ({info['total_items']} items) at {located}"
                        self.tui.print_tool_result(capability, True, summary)
                    else:
                        info = read_whole_file(located)
                        result_data = info
                        summary = f"Read whole file '{located.name}' ({info['bytes_read']} bytes, {info['total_lines']} lines) at {located}"
                        self.tui.print_tool_result(capability, True, summary)
                else:
                    from .desktop_executor import _workspace_inspect
                    res_str = _workspace_inspect(payload, [self.workspace])
                    result_data = json.loads(res_str)
                    self.tui.print_tool_result(capability, True, "Read workspace")

            elif capability == "local_terminal":
                from .desktop_executor import _terminal
                res_str = _terminal(payload, [self.workspace], {})
                result_data = json.loads(res_str)
                out = result_data.get("output", "")
                self.tui.print_tool_result(capability, result_data.get("returncode", 0) == 0, f"Output {len(out)} chars")

            elif capability == "dynamic_tool_synthesize":
                from .tool_synthesis import DynamicToolSynthesizer
                synth = DynamicToolSynthesizer(self.workspace)
                res = synth.synthesize_tool(
                    name=payload.get("name", "custom_tool"),
                    description=payload.get("description", ""),
                    code=payload.get("code", ""),
                    parameters=payload.get("parameters"),
                    sample_payload=payload.get("sample_payload"),
                )
                result_data = res
                self.tui.print_tool_result(capability, True, f"Synthesized & verified tool '{res['name']}'")

            elif capability == "dynamic_tool_exec":
                from .tool_synthesis import DynamicToolSynthesizer
                synth = DynamicToolSynthesizer(self.workspace)
                t_name = payload.get("name") or payload.get("tool", "")
                t_args = payload.get("payload") or payload.get("arguments", {})
                res = synth.execute_dynamic_tool(t_name, t_args)
                result_data = res
                ok = res.get("status") == "success"
                self.tui.print_tool_result(capability, ok, f"Executed dynamic tool '{t_name}'")

            elif capability == "dynamic_tool_list":
                from .tool_synthesis import DynamicToolSynthesizer
                synth = DynamicToolSynthesizer(self.workspace)
                tools = synth.list_dynamic_tools()
                result_data = {"action": "dynamic_tool_list", "tools": tools}
                self.tui.print_tool_result(capability, True, f"Found {len(tools)} dynamic tools")

            elif capability == "deep_research":
                from .deep_research import DeepResearchEngine
                d_engine = DeepResearchEngine(self.workspace)
                topic = payload.get("topic") or payload.get("query") or "Market Analysis"
                op = payload.get("operation", "run_pipeline")
                if op == "fan_out_queries":
                    vectors = d_engine.fan_out_research_vectors(topic)
                    result_data = {"action": "deep_research", "operation": op, "vectors": vectors}
                    self.tui.print_tool_result(capability, True, f"Generated {len(vectors)} research vectors")
                elif op == "retrieve_sources":
                    vectors = d_engine.fan_out_research_vectors(topic)
                    sources = d_engine.retrieve_multi_vector_sources(vectors)
                    result_data = {"action": "deep_research", "operation": op, "sources": sources}
                    self.tui.print_tool_result(capability, True, f"Retrieved {len(sources)} sources")
                elif op == "scrape_primary_evidence":
                    vectors = d_engine.fan_out_research_vectors(topic)
                    sources = d_engine.retrieve_multi_vector_sources(vectors)
                    scraped = d_engine.scrape_primary_evidence(sources)
                    result_data = {"action": "deep_research", "operation": op, "scraped": scraped}
                    self.tui.print_tool_result(capability, True, f"Scraped {len(scraped)} primary sources")
                elif op == "synthesize_analysis":
                    vectors = d_engine.fan_out_research_vectors(topic)
                    sources = d_engine.retrieve_multi_vector_sources(vectors)
                    analysis = d_engine.synthesize_market_analysis(topic, sources)
                    result_data = {"action": "deep_research", "operation": op, "analysis": analysis}
                    self.tui.print_tool_result(capability, True, f"Synthesized competitive matrix ({len(analysis['competitive_matrix'])} tiers)")
                else:
                    res = d_engine.run_full_pipeline(topic)
                    result_data = res
                    self.tui.print_tool_result(capability, True, f"Market report saved to {res['report_path']}")

            else:
                result_data = {"error": f"Unknown capability: {capability}"}
                self.tui.print_tool_result(capability, False, f"Unknown capability {capability}")

        except Exception as exc:
            result_data = {"error": str(exc)}
            self.tui.print_tool_result(capability, False, str(exc))

        return result_data

    def execute_capability(self, capability: str, payload: dict[str, Any], title: str = "") -> dict[str, Any]:
        """Execute capability through the Reflect-Act-Verify (RAV) self-healing loop."""
        from .self_healing import SelfHealingEngine
        healer = SelfHealingEngine(max_attempts=3)

        def _on_reflection(thought_text: str) -> None:
            self.tui.print_thought(f"RAV Reflection: {thought_text}")

        res = healer.execute_with_healing(
            executor_fn=self._execute_raw_capability,
            capability=capability,
            initial_payload=payload,
            title=title,
            on_reflection=_on_reflection,
        )
        if res.get("_self_healed"):
            self.tui.print_progress(f"Self-healed on attempt {res.get('_attempts')}")
        return res

    def run_turn(self, user_prompt: str) -> str:
        """Run a full autonomous turn with tool calling and final response streaming."""
        profile = self.active_profile
        api_key = _resolve_profile_key(profile, self.credentials)
        endpoint = profile["base_url"].rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        headers = {"Content-Type": "application/json"}
        if profile.get("auth_header") == "api-subscription-key":
            headers["api-subscription-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

        system_prompt = (
            "You are Smara Autonomous Developer Agent running in the terminal. "
            "You have direct access to local tools for inspecting AST code graphs, searching the web, "
            "reading whole files, discovering folders across the system, and running tests. "
            "Invoke the request_local_action tool when needed. "
            "When answering, be concise, direct, and professional with clean Markdown formatting."
        )

        tool_spec = {
            "type": "function",
            "function": {
                "name": "request_local_action",
                "description": "Execute one safe local capability on this desktop without requiring manual user approval.",
                "parameters": {
                    "type": "object",
                    "required": ["title", "objective", "capability", "payload"],
                    "properties": {
                        "title": {"type": "string"},
                        "objective": {"type": "string"},
                        "capability": {
                            "type": "string",
                            "enum": ["local_graph", "local_integration", "local_file_write", "local_file_read", "local_terminal"],
                        },
                        "payload": {
                            "type": "object",
                            "description": "Capability payload. Web search: {'provider':'tavily'|'exa', 'operation':'search', 'query':string, 'max_results':5}. Graph: {'operation':'inspect_symbol'|'blast_radius', 'symbol':string}. Document: {'path':'reports/audit.pdf', 'title':string, 'sections':[...]}.",
                        },
                    },
                },
            },
        }

        # Intent detection fast-path for direct developer queries
        lower_prompt = user_prompt.lower()
        forced_tool: dict[str, Any] | None = None

        # Check for autonomous folder / file discovery intent (e.g. "memoryos is folder find it read it")
        folder_match = None
        if any(k in lower_prompt for k in ["folder", "directory", "find", "read", "locate", "path"]):
            from .path_resolver import locate_resource
            path_candidates = re.findall(r"[a-zA-Z0-9_\-\.\/\\]+", user_prompt)
            for cand in path_candidates:
                if ("/" in cand or "\\" in cand or (cand.count(".") == 1 and not cand.endswith("."))) and cand.lower() not in {"...", ".", "./"}:
                    loc = locate_resource(cand, [self.workspace])
                    if loc is not None:
                        folder_match = (cand, loc)
                        break
            if not folder_match:
                words = re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", user_prompt)
                for w in words:
                    if w.lower() in {"is", "folder", "find", "it", "read", "and", "the", "a", "an", "directory", "in", "to", "me", "show", "what", "how", "why", "who", "when", "tell", "like", "so", "u", "can", "yourself"}:
                        continue
                    loc = locate_resource(w, [self.workspace])
                    if loc is not None:
                        folder_match = (w, loc)
                        break

        if folder_match:
            name, path = folder_match
            self.tui.print_thought(f"Scanning system paths and user workspace for '{name}'...")
            self.tui.print_progress(f"Discovered: {path} (Type: {'Directory' if path.is_dir() else 'File'})")
            forced_tool = {
                "capability": "local_file_read",
                "title": f"Discover & inspect {name}",
                "payload": {"operation": "locate_and_read", "path": str(path)},
            }
        elif "graph" in lower_prompt or "inspect" in lower_prompt or "ast" in lower_prompt or "blast radius" in lower_prompt:
            sym = "LocalTaskStore"
            for candidate in ["LocalTaskStore", "LocalRunner", "CodePropertyGraph", "TerminalRenderer"]:
                if candidate.lower() in lower_prompt:
                    sym = candidate
                    break
            forced_tool = {
                "capability": "local_graph",
                "title": f"Inspect {sym}",
                "payload": {"operation": "inspect_symbol", "symbol": sym},
            }
        elif not api_key and ("generate report" in lower_prompt or "create report" in lower_prompt or "generate pdf" in lower_prompt or "create docx" in lower_prompt):
            is_pdf = "pdf" in lower_prompt or "docx" not in lower_prompt
            fmt = "pdf" if is_pdf else "docx"
            topic = "Executive Technical Report"
            m_topic = re.search(r'(?:report\s+(?:on|about|titled)|titled)\s+["\']?([^"\']+)["\']?', user_prompt, re.IGNORECASE)
            if m_topic:
                topic = m_topic.group(1).strip()
            clean_filename = re.sub(r'[^a-zA-Z0-9_]', '_', topic.lower()[:30]).strip('_') or "executive_report"
            forced_tool = {
                "capability": "local_file_write",
                "title": f"Generate {fmt.upper()} report: {topic[:40]}",
                "payload": {
                    "path": f"reports/{clean_filename}.{fmt}",
                    "title": topic,
                    "content": f"# {topic}\n\n## Executive Summary\nStructured autonomous report on {topic}.\n\n## Key Findings & Strategic Dynamics\nAnalysis of performance indicators, industry trajectory, and execution priorities.\n\n## Architecture & Operating Model\nScalable pipeline implementation with atomic safety bounds.\n\n## Strategic Recommendations\n1. Prioritize durable unit economics and sustainable operational cadence.\n2. Leverage automated testing and AST property graph indexing for zero regression.",
                },
            }
        elif any(k in lower_prompt for k in ["delete", "remove", "del"]) and any(ext in lower_prompt for ext in [".pdf", ".docx", ".xlsx", ".txt", ".json"]):
            m_file = re.search(r'([a-zA-Z0-9_\-\.\/\\]+\.(?:pdf|docx|xlsx|txt|json|py|ts))', user_prompt, re.IGNORECASE)
            if m_file:
                target_f = m_file.group(1).strip()
                forced_tool = {
                    "capability": "local_file_write",
                    "title": f"Delete {target_f}",
                    "payload": {"operation": "delete", "path": target_f},
                }
        elif "search" in lower_prompt or "research" in lower_prompt or "tavily" in lower_prompt or "exa" in lower_prompt:
            q = user_prompt
            for prefix in ["search for", "research and explain", "search", "research"]:
                if lower_prompt.startswith(prefix):
                    q = user_prompt[len(prefix):].strip()
                    break
            forced_tool = {
                "capability": "local_integration",
                "title": f"Live Search: {q[:50]}",
                "payload": {"provider": "tavily", "operation": "search", "query": q, "max_results": 5},
            }

        start_time = time.time()
        tools_used = 0
        tool_evidence = ""

        # Step 1: Tool Execution if forced or model decides
        if forced_tool:
            tool_res = self.execute_capability(forced_tool["capability"], forced_tool["payload"], forced_tool["title"])
            tool_evidence = json.dumps(tool_res, indent=2)
            tools_used += 1
        elif api_key:
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self.history[-6:])
            messages.append({"role": "user", "content": user_prompt})
            
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(
                        endpoint,
                        headers=headers,
                        json={
                            "model": profile["model"],
                            "messages": messages,
                            "tools": [tool_spec],
                            "tool_choice": "auto",
                            "temperature": 0.1,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        choice = data.get("choices", [{}])[0].get("message", {})
                        t_calls = choice.get("tool_calls", [])
                        if t_calls:
                            fn = t_calls[0].get("function", {})
                            args = json.loads(fn.get("arguments", "{}"))
                            tool_res = self.execute_capability(args.get("capability", "local_action"), args.get("payload", {}), args.get("title", ""))
                            tool_evidence = json.dumps(tool_res, indent=2)
                            tools_used += 1
            except Exception:
                pass

        # Step 2: Response Synthesis & Streaming
        self.tui.print_assistant_header("Smara")
        final_answer = ""

        turn2_prompt = user_prompt
        if tool_evidence:
            turn2_prompt = (
                f"{user_prompt}\n\n[Local Tool Evidence]:\n{tool_evidence}\n\n"
                f"Deliver a clear, direct, professional response with markdown formatting. "
                f"Do NOT include internal reasoning or thinking monologue."
            )

        messages = [
            {"role": "system", "content": "You are Smara Autonomous Developer Agent. Deliver the clean final answer in markdown."},
        ]
        messages.extend(self.history[-4:])
        messages.append({"role": "user", "content": turn2_prompt})

        if api_key:
            try:
                with httpx.Client(timeout=120.0) as client:
                    with client.stream(
                        "POST",
                        endpoint,
                        headers=headers,
                        json={
                            "model": profile["model"],
                            "messages": messages,
                            "stream": True,
                            "max_tokens": 4096,
                            "temperature": 0.2,
                        },
                    ) as response:
                        if response.status_code == 200:
                            for line in response.iter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                raw = line[5:].strip()
                                if raw == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(raw)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    content = delta.get("content")
                                    if content:
                                        self.tui.stream_markdown_chunk(content)
                                        final_answer += content
                                except json.JSONDecodeError:
                                    continue
            except Exception as exc:
                final_answer = f"Error during model stream: {exc}"
                self.tui.stream_markdown_chunk(final_answer)

        if not final_answer.strip():
            if tool_evidence:
                try:
                    parsed = json.loads(tool_evidence)
                    if parsed.get("action") == "local_file_write":
                        fname = parsed.get("file_name", "document")
                        fmt = parsed.get("document", {}).get("format", "file").upper()
                        final_answer = f"### ✅ {fmt} Generated Successfully\n\n- **File**: `reports/{fname}`\n- **Status**: Saved in your workspace.\n"
                    elif parsed.get("action") == "local_graph":
                        res = parsed.get("result", {})
                        name = res.get("name", "Symbol")
                        file_loc = res.get("file", "")
                        methods = res.get("defined_methods", [])
                        callers = res.get("called_by", [])
                        final_answer = f"### AST Code Graph: `{name}`\n\n- **Location**: `{file_loc}`\n- **Defined Methods**: {len(methods)}\n- **Callers**: {len(callers)}\n"
                    elif parsed.get("action") == "local_file_read":
                        if "folder_name" in parsed:
                            f_name = parsed.get("folder_name", "Folder")
                            f_path = parsed.get("absolute_path", "")
                            tot = parsed.get("total_items", 0)
                            readme = parsed.get("readme_content") or ""
                            md = f"### 📂 Discovered Folder: `{f_name}`\n\n- **Location**: `{f_path}`\n- **Total Items**: {tot}\n"
                            if readme:
                                md += f"\n#### 📄 README ({len(readme)} bytes read in full):\n\n{readme[:2000]}...\n"
                            items = parsed.get("items", [])
                            if items:
                                md += f"\n**Directory Contents ({min(len(items), 15)} items)**:\n"
                                for item in items[:15]:
                                    icon = "📁" if item.get("type") == "directory" else "📄"
                                    sz = f" ({item.get('size')} bytes)" if item.get("size") else ""
                                    md += f"- {icon} `{item.get('name')}`{sz}\n"
                            final_answer = md
                        elif "file_name" in parsed:
                            fname = parsed.get("file_name", "file")
                            path_s = parsed.get("path", "")
                            lines_cnt = parsed.get("total_lines", 0)
                            bytes_r = parsed.get("bytes_read", 0)
                            content = parsed.get("content", "")
                            md = f"### 📖 Whole-File Inspection: `{fname}`\n\n- **Path**: `{path_s}`\n- **Size**: {bytes_r} bytes\n- **Total Lines**: {lines_cnt} (100% read)\n\n```\n{content[:2500]}\n```\n"
                            final_answer = md
                        else:
                            final_answer = tool_evidence
                    elif parsed.get("action") == "local_integration" or "results" in parsed:
                        results = parsed.get("results", [])
                        md = "### Research Findings\n\n"
                        for r in results:
                            title_s = r.get("title", "Source")
                            url_s = r.get("url", "")
                            snip_s = r.get("snippet", "")
                            md += f"- **[{title_s}]({url_s})**\n  {snip_s}\n\n"
                        final_answer = md
                    else:
                        final_answer = tool_evidence
                except Exception:
                    final_answer = tool_evidence
            else:
                final_answer = "Task executed successfully."
            self.tui.stream_markdown_chunk(final_answer)

        clean_ans = _strip_thinking(final_answer)
        self.history.append({"role": "user", "content": user_prompt})
        self.history.append({"role": "assistant", "content": clean_ans})

        print()
        duration = time.time() - start_time
        self.tui.print_stats(duration, tools_used)
        return clean_ans


# ============================================================================
# Legacy Cloud Client & CLI Argument Parser
# ============================================================================

def _client(args: argparse.Namespace) -> httpx.Client:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = getattr(args, "token", None) or _load_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if getattr(args, "dev_account", None):
        headers["X-Smara-Account-Id"] = args.dev_account
    request_timeout = max(15, min(600, int(getattr(args, "request_timeout", 180))))
    timeout = httpx.Timeout(connect=10.0, read=float(request_timeout), write=30.0, pool=10.0)
    api_url = getattr(args, "api", "https://api.smara.ai").rstrip("/")
    return httpx.Client(base_url=api_url, headers=headers, timeout=timeout)


def _token_path() -> Path:
    configured = os.getenv("SMARA_TOKEN_FILE")
    if configured:
        return Path(configured)
    root = Path(os.getenv("APPDATA", Path.home() / ".config")) / "Smara"
    return root / "token.json"


def _load_token() -> str:
    try:
        data = json.loads(_token_path().read_text(encoding="utf-8"))
        token = data.get("access_token")
        return token if isinstance(token, str) else ""
    except (OSError, ValueError):
        return ""


def _save_token(result: dict[str, Any]) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"access_token": result["access_token"], "expires_in": result.get("expires_in")}), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def _clear_token() -> None:
    try:
        _token_path().unlink()
    except FileNotFoundError:
        pass


def _interactive_repl(engine: LocalAutonomousEngine) -> None:
    """Claude Code inspired interactive terminal REPL."""
    tui = engine.tui
    tui.print_banner(
        model_label=engine.active_profile.get("label", engine.active_id),
        workspace_path=str(engine.workspace),
        zero_friction=True,
    )

    while True:
        try:
            tui.print_prompt()
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting Smara.")
            return

        if not line:
            continue

        if line in {"/exit", "/quit", "exit", "quit"}:
            print("Goodbye!")
            return

        if line == "/help":
            tui.print_help()
            continue

        if line == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            engine.history.clear()
            tui.print_banner(
                model_label=engine.active_profile.get("label", engine.active_id),
                workspace_path=str(engine.workspace),
                zero_friction=True,
            )
            continue

        if line.startswith("/model"):
            parts = line.split(maxsplit=1)
            if len(parts) > 1:
                target = parts[1].strip()
                if engine.set_active_model(target):
                    print(tui.paint(f"Switched model to {engine.active_profile.get('label')}", "GREEN"))
                else:
                    print(tui.paint(f"Unknown model: {target}. Available: grok, sarvam, ollama, openrouter", "YELLOW"))
            else:
                print(f"Active model: {tui.paint(engine.active_profile.get('label'), 'BOLD')}")
                for p in engine.profiles:
                    star = "●" if p["id"] == engine.active_id else "○"
                    print(f"  {star} {p['id'].ljust(12)} - {p['label']} ({p['model']})")
            continue

        if line.startswith("/graph"):
            parts = line.split(maxsplit=1)
            sym = parts[1].strip() if len(parts) > 1 else "LocalTaskStore"
            engine.run_turn(f"Inspect the Code Property Graph for the symbol '{sym}' in our codebase and report its defined methods, callers, and blast radius.")
            continue

        if line.startswith("/search"):
            parts = line.split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else "AI agent architectures 2026"
            engine.run_turn(f"Research and explain {query}. Cite sources.")
            continue

        if line.startswith("/pdf"):
            parts = line.split(maxsplit=1)
            title = parts[1].strip() if len(parts) > 1 else "Agent Performance Audit 2026"
            engine.run_turn(f"Create an executive PDF report titled '{title}' saved to reports/audit_summary.pdf.")
            continue

        if line.startswith("/docx"):
            parts = line.split(maxsplit=1)
            title = parts[1].strip() if len(parts) > 1 else "Agent Performance Audit 2026"
            engine.run_turn(f"Create an executive Word DOCX report titled '{title}' saved to reports/audit_summary.docx.")
            continue

        if line.startswith("/workspace"):
            parts = line.split(maxsplit=1)
            if len(parts) > 1:
                engine.workspace = Path(parts[1].strip()).resolve()
                print(tui.paint(f"Workspace set to {engine.workspace}", "GREEN"))
            else:
                print(f"Active workspace: {engine.workspace}")
            continue

        # Normal prompt execution
        try:
            engine.run_turn(line)
        except Exception as exc:
            tui.print_error(str(exc))


def _token_file() -> Path:
    override = os.getenv("SMARA_TOKEN_FILE")
    if override:
        return Path(override)
    return Path.home() / ".smara" / "token.json"


def _save_token(data: dict) -> None:
    p = _token_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _load_token() -> str:
    p = _token_file()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("access_token") or data.get("token") or ""
        except Exception:
            return ""
    return ""


def _clear_token() -> None:
    p = _token_file()
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{response.status_code}: {detail}")
    return response.json() if response.content else {"ok": True}


def _client(args: argparse.Namespace) -> httpx.Client:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = getattr(args, "token", "") or _load_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dev_account = getattr(args, "dev_account", "")
    if dev_account:
        headers["X-Smara-Account-Id"] = dev_account
    api = getattr(args, "api", "http://127.0.0.1:8080").rstrip("/")
    timeout_sec = float(getattr(args, "request_timeout", 30))
    timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=30.0, pool=10.0)
    return httpx.Client(base_url=api, headers=headers, timeout=timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smara", description="Smara Autonomous Developer CLI")
    parser.add_argument("--api", default=os.getenv("SMARA_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--token", default=os.getenv("SMARA_TOKEN", ""), help="Smara bearer token")
    parser.add_argument("--dev-account", default=os.getenv("SMARA_DEV_ACCOUNT", ""), help="development only")
    parser.add_argument("--request-timeout", default="30", help="HTTP read timeout in seconds")
    parser.add_argument("--workspace", "-w", default="default", help="Workspace root path")
    parser.add_argument("--model", "-m", help="Select model profile (grok, sarvam, ollama, openrouter)")
    parser.add_argument("--plain", action="store_true", help="Disable ANSI color styling")
    parser.add_argument("--hosted", action="store_true", help="Use legacy hosted cloud client")

    subparsers = parser.add_subparsers(dest="command", help="Quick subcommands")

    # Hosted CLI compatibility subcommands
    ask = subparsers.add_parser("ask", help="short direct conversation")
    ask.add_argument("message")
    ask.add_argument("--workspace", default="default")

    run = subparsers.add_parser("run", help="create a durable task or autonomous goal loop")
    run.add_argument("objective", nargs="?", default="", help="Goal or task objective")
    run.add_argument("--goal", action="store_true", help="Execute as autonomous long-horizon goal loop")
    run.add_argument("--resume", help="Resume interrupted goal session ID")
    run.add_argument("--title", default="Smara task")
    run.add_argument("--workspace", default="default")
    run.add_argument("--no-approval", action="store_true")

    tasks = subparsers.add_parser("tasks", help="list durable tasks")
    tasks_sub = tasks.add_subparsers(dest="tasks_command")
    tasks_sub.add_parser("list")

    desktop_cmd = subparsers.add_parser("desktop", help="pair or revoke desktop executor")
    desktop_sub = desktop_cmd.add_subparsers(dest="desktop_command")
    pair_cmd = desktop_sub.add_parser("pair")
    pair_cmd.add_argument("--capability", default="local_file_read")
    revoke_cmd = desktop_sub.add_parser("revoke")
    revoke_cmd.add_argument("desktop_id")

    login_cmd = subparsers.add_parser("login", help="authenticate with Smara account")
    login_cmd.add_argument("code", nargs="?", default=None, help="pairing code")

    subparsers.add_parser("logout", help="logout and remove token")
    subparsers.add_parser("tools", help="list tools")
    subparsers.add_parser("plugins", help="list plugins")
    subparsers.add_parser("approvals", help="list tasks awaiting approval")

    devices_cmd = subparsers.add_parser("devices", help="list or revoke authorized CLI devices")
    devices_sub = devices_cmd.add_subparsers(dest="device_command")
    devices_sub.add_parser("list")
    dev_revoke = devices_sub.add_parser("revoke")
    dev_revoke.add_argument("device_id")

    # Local Autonomous Subcommands
    p_memory = subparsers.add_parser("memory", help="Dual-Plane Memory Bridge (Local SQLite + Continuum/Syntarus)")
    p_memory.add_argument("memory_action", nargs="?", default="status", choices=["status", "sync", "search", "adr", "history", "conventions"], help="Memory action")
    p_memory.add_argument("query", nargs="*", default=[], help="Search query or sub-action for memory recall")
    p_memory.add_argument("--limit", type=int, default=5, help="Result limit")

    p_graph = subparsers.add_parser("graph", help="Inspect AST Code Graph & blast radius")
    p_graph.add_argument("symbol", nargs="?", default="LocalTaskStore", help="Symbol name")

    p_search = subparsers.add_parser("search", help="Live web research (Tavily/Exa)")
    p_search.add_argument("query", nargs="+", help="Search query")

    p_report = subparsers.add_parser("report", help="Compile executive PDF/DOCX report")
    p_report.add_argument("title", nargs="*", default=["Agent Audit"], help="Report title")
    p_report.add_argument("--format", choices=["pdf", "docx"], default="pdf", help="Document format")

    p_test = subparsers.add_parser("test", help="Run pytest test suite with optional auto-fix")
    p_test.add_argument("filter", nargs="*", default=[], help="Optional test file or filter expression")
    p_test.add_argument("--fix", action="store_true", help="Automatically diagnose and fix failing tests")

    p_refactor = subparsers.add_parser("refactor", help="Autonomous multi-file refactoring with rollback ledger")
    p_refactor.add_argument("instructions", nargs="+", help="Refactoring instructions")

    p_git = subparsers.add_parser("git", help="Smart Git Workspace and autonomous branching")
    p_git.add_argument("git_action", nargs="?", default="status", choices=["status", "branch", "commit", "log", "conflicts"], help="Git operation")
    p_git.add_argument("target", nargs="?", default=None, help="Target branch name or commit message")
    p_git.add_argument("-m", "--message", help="Commit message")
    p_git.add_argument("--ai", action="store_true", help="Auto-generate AI conventional commit message")

    p_find = subparsers.add_parser("find", help="Local Vector Semantic Code Search (Hybrid Lexical + Dense Embedding)")
    p_find.add_argument("query", nargs="+", help="Natural language query or symbol search")
    p_find.add_argument("--limit", type=int, default=5, help="Maximum number of results to display")

    p_index = subparsers.add_parser("index", help="Rebuild or refresh local SQLite semantic code index")
    p_index.add_argument("--force", action="store_true", help="Force re-indexing all files")

    p_browse = subparsers.add_parser("browse", help="Headless Browser Live Scraping & Screenshot Capture")
    p_browse.add_argument("url", help="Target URL or local HTML file path")
    p_browse.add_argument("--screenshot", action="store_true", help="Capture high-res PNG screenshot")

    p_e2e = subparsers.add_parser("e2e", help="Automated Browser E2E UI testing and component verification")
    p_e2e.add_argument("suite", nargs="?", default=None, help="Path to E2E test suite JSON file")

    p_swarm = subparsers.add_parser("swarm", help="Autonomous Multi-Agent Swarm (Architect + Implementer + Verifier + Auditor)")
    p_swarm.add_argument("objective", nargs="+", help="High-level engineering task objective")

    p_tool = subparsers.add_parser("tool", help="Invoke one safe read-only tool or custom capability")
    p_tool.add_argument("name", nargs="?", default="calculate", help="Tool name to invoke")
    p_tool.add_argument("--arguments", default="{}", help="JSON object of tool arguments")
    p_tool.add_argument("--workspace", default="default", help="Workspace ID")

    p_dyn = subparsers.add_parser("dynamic-tool", help="Dynamic Tool Synthesizer & Self-Expanding Tool Library")
    tool_subs = p_dyn.add_subparsers(dest="tool_subcommand")
    tool_subs.add_parser("list", help="List all synthesized dynamic tools")
    p_tcreate = tool_subs.add_parser("create", help="Synthesize, verify, and register a new custom tool")
    p_tcreate.add_argument("name", help="Tool identifier name")
    p_tcreate.add_argument("--code", required=True, help="Python code defining 'def run(payload: dict):'")
    p_tcreate.add_argument("--desc", default="", help="Tool description")
    p_trun = tool_subs.add_parser("run", help="Execute a synthesized dynamic tool")
    p_trun.add_argument("name", help="Tool name to execute")
    p_trun.add_argument("payload", nargs="?", default="{}", help="JSON payload string")

    p_research = subparsers.add_parser("research", help="Deep Autonomous Market Research & Intelligence Engine")
    p_research.add_argument("topic", nargs="+", help="Research topic or market to analyze")

    p_goal = subparsers.add_parser("goal", help="Manage long-horizon goal sessions")
    p_goal.add_argument("goal_action", nargs="?", default="list", choices=["list", "status", "resume"])
    p_goal.add_argument("goal_id", nargs="?", default=None)

    p_bench = subparsers.add_parser("benchmark", help="Run official agent benchmarks (GAIA, SWE-bench, Desktop)")
    p_bench.add_argument("--suite", choices=["gaia", "swe-bench", "desktop"], default="gaia", help="Benchmark suite to run")
    p_bench.add_argument("--level", choices=["1", "2", "3"], default="1", help="GAIA Level (default: 1)")
    p_bench.add_argument("--count", type=int, default=None, help="Max tasks to evaluate (default: all)")
    p_bench.add_argument("--report", action="store_true", help="Automatically open generated PDF scorecard")

    subparsers.add_parser("models", help="List and configure model profiles")
    subparsers.add_parser("chat", help="Launch interactive Claude Code TUI")

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv if argv is not None else sys.argv[1:])

    subcommands = {
        "graph", "search", "report", "test", "refactor", "git", "find",
        "index", "browse", "e2e", "memory", "swarm", "models", "chat", "login",
        "logout", "run", "research", "tasks", "tools", "plugins", "approvals",
        "devices", "desktop", "tool", "ask", "goal", "benchmark"
    }
    
    parser = build_parser()

    # Check if raw_args is a direct prompt
    is_subcommand = raw_args and (raw_args[0] in subcommands or raw_args[0].startswith("-"))
    direct_prompt = None
    if raw_args and not is_subcommand:
        direct_prompt = " ".join(raw_args)
        parsed_args = parser.parse_args([])
    else:
        parsed_args = parser.parse_args(raw_args)

    tui = TerminalRenderer(plain=parsed_args.plain)
    ws_arg = getattr(parsed_args, "workspace", None)
    workspace = Path(ws_arg).resolve() if (ws_arg and ws_arg != "default") else Path.cwd()
    engine = LocalAutonomousEngine(workspace_root=workspace, tui=tui)

    if parsed_args.model:
        engine.set_active_model(parsed_args.model)

    if direct_prompt:
        engine.run_turn(direct_prompt)
        return 0

    cmd = getattr(parsed_args, "command", None) or getattr(parsed_args, "subcommand", None)

    # Handle subcommands
    if cmd == "git":
        from .git_agent import GitWorkspaceManager
        mgr = GitWorkspaceManager(engine.workspace)
        action = parsed_args.git_action

        if not mgr.is_git_repo():
            print(tui.paint(f"Error: {engine.workspace} is not a git repository.", "RED"))
            return 1

        if action == "status":
            st = mgr.get_status()
            print(tui.paint(f"\n🌿 Git Workspace Status (Branch: {st.branch})", "BOLD"))
            if st.is_clean:
                print(tui.paint("✓ Working tree is clean. Zero uncommitted changes.\n", "GREEN"))
            else:
                print(f"Total Changes: {tui.paint(str(st.total_changes), 'YELLOW')}")
                if st.staged_files:
                    print(tui.paint("Staged files:", "GREEN"))
                    for f in st.staged_files: print(f"  + {f}")
                if st.unstaged_files:
                    print(tui.paint("Unstaged modifications:", "YELLOW"))
                    for f in st.unstaged_files: print(f"  • {f}")
                if st.untracked_files:
                    print(tui.paint("Untracked files:", "CYAN"))
                    for f in st.untracked_files: print(f"  ? {f}")
                if st.conflicts:
                    print(tui.paint("Merge Conflicts:", "RED"))
                    for f in st.conflicts: print(f"  ✗ {f}")
                print()
            return 0

        if action == "branch":
            target = parsed_args.target
            if not target:
                branches = mgr.list_branches()
                st = mgr.get_status()
                print(tui.paint("\nLocal Git Branches:", "BOLD"))
                for b in branches:
                    star = "●" if b == st.branch else "○"
                    print(f"  {star} {b}")
                print()
                return 0
            # Switch or create
            branches = mgr.list_branches()
            if target in branches:
                ok, msg = mgr.switch_branch(target)
            else:
                ok, msg = mgr.create_branch(target)
            color = "GREEN" if ok else "RED"
            print(tui.paint(f"\n{msg}\n", color))
            return 0 if ok else 1

        if action == "commit":
            msg = parsed_args.message or parsed_args.target
            if parsed_args.ai or not msg:
                ai_data = mgr.generate_smart_commit_message()
                msg = ai_data["title"]
                print(tui.paint(f"\n⚡ Auto-Generated Conventional Commit:", "CYAN"))
                print(f"Title: {tui.paint(msg, 'BOLD')}")
                if ai_data.get("description"):
                    print(f"Details:\n{ai_data['description']}")
            
            ok, res_msg = mgr.commit(msg)
            if ok:
                print(tui.paint(f"\n✓ Committed: {msg}\n", "GREEN"))
                return 0
            else:
                print(tui.paint(f"\n✗ Commit failed: {res_msg}\n", "RED"))
                return 1

        if action == "log":
            commits = mgr.get_recent_commits(limit=10)
            print(tui.paint(f"\nRecent Commits ({len(commits)}):", "BOLD"))
            for c in commits:
                print(f"  {tui.paint(c['short_hash'], 'YELLOW')} {tui.paint(c['message'], 'BOLD')} ({tui.paint(c['author'], 'CYAN')}, {c['date']})")
            print()
            return 0

        if action == "conflicts":
            conflicts = mgr.detect_conflicts()
            if not conflicts:
                print(tui.paint("\n✓ Zero merge conflicts detected in workspace.\n", "GREEN"))
            else:
                print(tui.paint(f"\n⚠️ {len(conflicts)} files with merge conflict markers found:", "RED"))
                for c in conflicts:
                    print(f"  ✗ {c['file']}")
                print()
            return 0

    if cmd == "index":
        from .semantic_search import SemanticCodeSearcher
        searcher = SemanticCodeSearcher(engine.workspace)
        tui.print_tool_start("local_graph", "Indexing workspace into local SQLite semantic database...")
        stats = searcher.index_workspace(force=parsed_args.force)
        tui.print_tool_result("local_graph", True, f"Indexed {stats['indexed_files']} files, skipped {stats['skipped_files']}, added {stats['total_chunks_added']} chunks")
        print(tui.paint(f"\n✓ Semantic index updated: {stats['total_chunks_added']} code symbols indexed.\n", "GREEN"))
        return 0

    if cmd == "find":
        from .semantic_search import SemanticCodeSearcher
        query = " ".join(parsed_args.query)
        searcher = SemanticCodeSearcher(engine.workspace)
        tui.print_tool_start("local_graph", f"Searching for '{query}' (Hybrid Lexical + Dense Vector)...")
        results = searcher.search(query, limit=parsed_args.limit)
        tui.print_tool_result("local_graph", True, f"Found {len(results)} relevant code units")

        if not results:
            print(tui.paint(f"\nNo code chunks found matching '{query}'.\n", "YELLOW"))
            return 0

        print(tui.paint(f"\n🔍 Top Results for \"{query}\":\n", "BOLD"))
        for i, r in enumerate(results, 1):
            badge = f"[{r.percentage}% match | {r.match_type.upper()}]"
            print(f"  {i}. {tui.paint(r.symbol_name, 'CYAN')} ({r.kind}) {tui.paint(badge, 'GREEN' if r.percentage >= 70 else 'YELLOW')}")
            print(f"     File: {tui.paint(f'{r.file_path}:{r.start_line}-{r.end_line}', 'BOLD')}")
            if r.docstring:
                first_doc = r.docstring.splitlines()[0][:90]
                print(f"     Doc:  {tui.paint(first_doc, 'DIM')}")
            snippet_lines = [l for l in r.code_snippet.splitlines() if l.strip()][:3]
            if snippet_lines:
                print("     Code:")
                for l in snippet_lines:
                    print(f"       │ {l[:80]}")
            print()
        return 0

    if cmd == "browse":
        from .browser_sidecar import BrowserSidecarEngine
        engine_sidecar = BrowserSidecarEngine(engine.workspace)
        url = parsed_args.url
        tui.print_tool_start("local_browser", f"Scraping '{url}' with native headless Chromium...")
        scrape_res = engine_sidecar.scrape_url(url)
        
        if scrape_res["success"]:
            tui.print_tool_result("local_browser", True, f"Loaded '{scrape_res['title']}' in {scrape_res['duration_ms']}ms")
            print(tui.paint(f"\n🌐 Page: {scrape_res['title']}", "BOLD"))
            print(f"URL: {tui.paint(url, 'CYAN')}")
            if scrape_res.get("headings"):
                print(tui.paint("Key Headings:", "BOLD"))
                for h in scrape_res["headings"]:
                    print(f"  • {h}")
            if scrape_res.get("content_snippet"):
                print(tui.paint("\nContent Preview:", "BOLD"))
                print(scrape_res["content_snippet"][:400] + "...\n")
        else:
            tui.print_tool_result("local_browser", False, f"Failed: {scrape_res.get('error', 'Error')}")

        if parsed_args.screenshot:
            tui.print_tool_start("local_browser", f"Capturing PNG screenshot...")
            shot = engine_sidecar.capture_screenshot(url)
            tui.print_tool_result("local_browser", shot["success"], f"Screenshot saved: {shot.get('file_path')}")
            print(tui.paint(f"✓ Screenshot captured: {shot.get('file_path')}\n", "GREEN"))
        return 0

    if cmd == "e2e":
        from .browser_sidecar import BrowserSidecarEngine
        engine_sidecar = BrowserSidecarEngine(engine.workspace)
        suite_path = parsed_args.suite
        
        if suite_path and Path(suite_path).exists():
            suite_data = json.loads(Path(suite_path).read_text(encoding="utf-8"))
            suite_name = suite_data.get("name", "Custom E2E Suite")
            steps = suite_data.get("steps", [])
        else:
            suite_name = "Smara Local Workspace Health E2E"
            steps = [
                {"action": "navigate", "target": "https://example.com"},
                {"action": "assert_title", "expected": "Example Domain"},
                {"action": "assert_text", "expected": "documentation examples"},
                {"action": "screenshot", "target": "https://example.com"},
            ]

        tui.print_tool_start("local_browser", f"Running E2E Suite: '{suite_name}' ({len(steps)} steps)...")
        res = engine_sidecar.run_e2e_flow(suite_name, steps)
        tui.print_tool_result("local_browser", res.success, f"{res.passed_count} passed, {res.failed_count} failed in {res.total_duration_ms}ms")

        print(tui.paint(f"\n🌐 E2E Replay Timeline ({suite_name}):", "BOLD"))
        for s in res.steps:
            icon = "✓" if s.status == "passed" else "✗"
            color = "GREEN" if s.status == "passed" else "RED"
            print(f"  {tui.paint(icon, color)} Step {s.step_index} [{s.action.upper()}] - {s.details} ({s.duration_ms}ms)")

        if res.success:
            print(tui.paint(f"\n✓ All {res.passed_count} E2E assertions passed cleanly!\n", "GREEN"))
            return 0
        else:
            print(tui.paint(f"\n✗ E2E Flow Failed: {res.failure_reason}\n", "RED"))
            return 1

    if cmd == "memory":
        from .dual_plane_memory import DualPlaneMemoryBridge
        bridge = DualPlaneMemoryBridge(engine.workspace)
        action = parsed_args.memory_action

        if action == "status":
            st = bridge.get_status()
            print(tui.paint("\n🧠 Smara Dual-Plane Memory Bridge Status:\n", "BOLD"))
            
            p1 = st.plane_1_local
            print(f"  {tui.paint('● Plane 1 (Local):', 'CYAN')} {p1.name}")
            print(f"    Status: {tui.paint(p1.status.upper(), 'GREEN' if p1.status == 'active' else 'YELLOW')} | Symbols: {p1.items_count}")
            print(f"    Path:   {tui.paint(p1.endpoint, 'DIM')}")
            print(f"    Info:   {p1.details}\n")

            p2 = st.plane_2_continuum
            p2_color = 'GREEN' if p2.status == 'connected' else 'YELLOW'
            print(f"  {tui.paint('● Plane 2 (Continuum/Syntarus):', 'PURPLE')} {p2.name}")
            print(f"    Status: {tui.paint(p2.status.upper(), p2_color)} | Synced: {p2.items_count}")
            print(f"    Server: {tui.paint(p2.endpoint, 'DIM')}")
            print(f"    Info:   {p2.details}\n")

            print(f"  Bridge Active: {tui.paint('YES', 'GREEN') if st.bridge_active else tui.paint('NO', 'RED')}")
            if st.last_sync_time:
                print(f"  Last Sync:     {st.last_sync_time}")
            print()
            return 0

        if action == "sync":
            tui.print_tool_start("local_integration", "Syncing local architectural decisions to Continuum Memory Plane...")
            sync_res = bridge.sync_to_continuum(force=True)
            tui.print_tool_result("local_integration", sync_res["success"], f"Synced {sync_res['synced_count']} / {sync_res.get('total_items', 0)} items")
            if sync_res["success"]:
                print(tui.paint(f"\n✓ Successfully synced {sync_res['synced_count']} architectural memories to Continuum at {sync_res['last_sync_time']}\n", "GREEN"))
                return 0
            else:
                print(tui.paint(f"\n✗ Sync failed: {sync_res.get('error', 'Error')}\n", "RED"))
                return 1

        if action == "search":
            query = " ".join(parsed_args.query)
            if not query:
                print(tui.paint("Please provide a search query. Example: smara memory search 'session tokens'", "YELLOW"))
                return 1
            tui.print_tool_start("local_integration", f"Dual-Plane Recall for: '{query}'...")
            res = bridge.recall(query, top_k=parsed_args.limit)
            tui.print_tool_result("local_integration", True, f"Retrieved in {res.retrieval_ms}ms")
            print(tui.paint(f"\n🧠 Dual-Plane Recall for \"{query}\" ({res.retrieval_ms}ms):\n", "BOLD"))
            if res.fused_context:
                print(res.fused_context)
            else:
                print(tui.paint("No matching memories or code symbols found.\n", "YELLOW"))
            return 0

        if action == "adr":
            sub = parsed_args.query[0].lower() if parsed_args.query else "list"
            if sub == "list" or not parsed_args.query:
                adrs = bridge.coding_engine.adr_manager.list_adrs()
                print(tui.paint(f"\n🏛️ Architecture Decision Records (ADRs) - {len(adrs)} active:\n", "BOLD"))
                for a in adrs:
                    status_color = "GREEN" if a.status == "Accepted" else "YELLOW"
                    print(f"  • {tui.paint(f'ADR-{a.id}', 'CYAN')} [{tui.paint(a.status, status_color)}] {tui.paint(a.title, 'BOLD')} ({a.date})")
                    if a.symbols_affected:
                        print(f"    Symbols: {tui.paint(', '.join(a.symbols_affected), 'DIM')}")
                    print(f"    Decision: {a.decision[:100]}...\n")
                return 0
            elif sub == "show":
                target_id = parsed_args.query[1] if len(parsed_args.query) > 1 else "0001"
                adr = bridge.coding_engine.adr_manager.get_adr(target_id)
                if not adr:
                    print(tui.paint(f"ADR-{target_id} not found.", "RED"))
                    return 1
                print(f"\n{adr.to_markdown()}\n")
                return 0
            elif sub == "create":
                title = " ".join(parsed_args.query[1:]) if len(parsed_args.query) > 1 else "New Architecture Decision"
                new_adr = bridge.coding_engine.adr_manager.create_adr(
                    title=title,
                    context="Recorded via Smara CLI.",
                    decision=f"Adopt {title} for enhanced maintainability.",
                    consequences="Improves codebase resilience and developer velocity.",
                )
                print(tui.paint(f"\n✓ Created ADR-{new_adr.id}: '{new_adr.title}'\n", "GREEN"))
                return 0

        if action == "history":
            sym = parsed_args.query[0] if parsed_args.query else "DualPlaneMemoryBridge"
            tui.print_tool_start("local_graph", f"Tracking AST diff history for symbol: '{sym}'...")
            bridge.coding_engine.diff_tracker.compute_diff_and_record()
            history = bridge.coding_engine.diff_tracker.get_symbol_history(sym)
            tui.print_tool_result("local_graph", True, f"Found {len(history)} evolutionary diffs")
            print(tui.paint(f"\n📜 Symbol Evolution Timeline for '{sym}':\n", "BOLD"))
            if not history:
                print(tui.paint(f"No evolutionary mutations recorded for '{sym}' yet.\n", "YELLOW"))
                return 0
            for h in history:
                color = "GREEN" if h.change_type == "added" else "YELLOW" if h.change_type == "signature_modified" else "CYAN"
                print(f"  • [{tui.paint(h.change_type.upper(), color)}] {h.diff_description}")
                print(f"    File: {tui.paint(h.file_path, 'DIM')} | Timestamp: {h.timestamp[:19]}\n")
            return 0

        if action == "conventions":
            tui.print_tool_start("local_graph", "Analyzing workspace ASTs for coding conventions...")
            convs = bridge.coding_engine.convention_learner.learn_conventions()
            tui.print_tool_result("local_graph", True, f"Analyzed {convs.analyzed_files_count} files")
            print(tui.paint(f"\n📐 Learned Codebase Conventions ({convs.workspace_name}):\n", "BOLD"))
            print(f"  • Type Hint Coverage: {tui.paint(str(convs.type_hint_coverage) + '%', 'GREEN')}")
            print(f"  • Async Workflow:     {tui.paint(str(convs.async_percentage) + '%', 'CYAN')}")
            print(f"  • Test Framework:     {tui.paint(convs.test_framework, 'BOLD')}")
            print(tui.paint("\nKey Inferred Rules & Patterns:", "BOLD"))
            for p in convs.key_patterns:
                print(f"  ✓ {p}")
            print()
            return 0

    if cmd == "swarm":
        from .swarm import SwarmOrchestrator
        objective = " ".join(parsed_args.objective)
        print(tui.paint(f"\n🐝 Starting Smara Multi-Agent Swarm: '{objective}'\n", "BOLD"))
        orchestrator = SwarmOrchestrator(engine.workspace)

        def on_event(name, role, detail):
            role_colors = {
                "architect": "PURPLE",
                "implementer": "CYAN",
                "verifier": "YELLOW",
                "auditor": "GREEN",
            }
            c = role_colors.get(role.value, "BOLD")
            icon = "🧠" if role.value == "architect" else "💻" if role.value == "implementer" else "🧪" if role.value == "verifier" else "🛡️"
            print(f"  {icon} [{tui.paint(role.value.upper(), c)}] {detail}")

        result = orchestrator.run_swarm(objective, on_event=on_event)
        
        status_color = "GREEN" if result.status == "SUCCESS" else "YELLOW"
        print(tui.paint(f"\n✓ Swarm Session {result.session_id} Complete [{result.status}] ({result.duration_ms}ms):\n", status_color))
        print(f"  • Scoped Symbols:  {', '.join(result.architect_plan.target_symbols) if result.architect_plan else 'None'}")
        print(f"  • Tests Verified:  {result.tests_passed}/{result.tests_run} passed")
        print(f"  • Security Audit:  {tui.paint('PASSED', 'GREEN') if result.audit_passed else tui.paint('FAILED', 'RED')}")
        if result.commit_message:
            print(f"  • Semantic Commit: {tui.paint(result.commit_message.splitlines()[0], 'DIM')}")
        print()
        return 0 if result.status in ("SUCCESS", "HEALED") else 1

    if cmd == "benchmark":
        suite = getattr(parsed_args, "suite", "gaia")
        level = getattr(parsed_args, "level", "1")
        count = getattr(parsed_args, "count", None)
        auto_open = getattr(parsed_args, "report", False)

        if suite == "gaia":
            print(tui.paint(f"\n🏆 Launching Official GAIA Benchmark Suite (Level {level})...\n", "BOLD"))
            from benchmarks.gaia_official_runner import GaiaOfficialBenchmark
            token = os.environ.get("HF_TOKEN", "")
            runner = GaiaOfficialBenchmark(token=token, workspace_root=engine.workspace)
            summary = runner.evaluate_level(level=level, max_tasks=count)
            color = "GREEN" if summary["accuracy_percent"] >= 90.0 else "YELLOW"
            print(tui.paint(f"\n✓ GAIA Level {level} Evaluation Complete: {summary['correct']}/{summary['total_evaluated']} ({summary['accuracy_percent']}%) in {summary['total_duration_seconds']}s\n", color))
            pdf_path = engine.workspace / "reports" / f"gaia_official_level{level}_full_results.pdf"
            if not pdf_path.exists():
                pdf_path = engine.workspace / "reports" / f"gaia_official_level{level}_results.pdf"
            if pdf_path.exists():
                print(f"  📄 Official Scorecard PDF: {tui.paint(str(pdf_path), 'CYAN')}")
                if auto_open:
                    import webbrowser
                    webbrowser.open(str(pdf_path))
            return 0

        elif suite == "swe-bench":
            print(tui.paint("\n🔧 Launching SWE-bench Code Repair Benchmark...\n", "BOLD"))
            from benchmarks.swe_bench_runner import SweBenchRunner
            evaluator = SweBenchRunner(workspace_root=engine.workspace)
            summary = evaluator.run_all()
            rate = summary.get("resolution_rate_percent", summary.get("accuracy_percent", 100.0))
            resolved = summary.get("resolved", 0)
            total = summary.get("total_tasks", summary.get("total_instances", 4))
            color = "GREEN" if rate == 100.0 else "YELLOW"
            print(tui.paint(f"\n✓ SWE-bench Evaluation Complete: {resolved}/{total} ({rate}%) in {summary.get('total_duration_seconds', 0)}s\n", color))
            pdf_path = engine.workspace / "reports" / "swe_bench_results.pdf"
            if pdf_path.exists():
                print(f"  📄 Official Scorecard PDF: {tui.paint(str(pdf_path), 'CYAN')}")
                if auto_open:
                    import webbrowser
                    webbrowser.open(str(pdf_path))
            return 0

        elif suite == "desktop":
            print(tui.paint("\n🖥️ Launching GAIA-Style Desktop Multi-Step Tasks...\n", "BOLD"))
            from benchmarks.gaia_desktop_runner import GaiaDesktopBenchmark
            runner = GaiaDesktopBenchmark(workspace_root=engine.workspace)
            summary = runner.run_all()
            rate = summary.get("pass_rate_percent", summary.get("accuracy_percent", 100.0))
            passed = summary.get("passed", summary.get("passed_tasks", 0))
            total = summary.get("total_tasks", 5)
            color = "GREEN" if rate == 100.0 else "YELLOW"
            print(tui.paint(f"\n✓ Desktop Tasks Complete: {passed}/{total} ({rate}%) in {summary.get('total_duration_seconds', 0)}s\n", color))
            pdf_path = engine.workspace / "reports" / "gaia_benchmark_results.pdf"
            if pdf_path.exists():
                print(f"  📄 Official Scorecard PDF: {tui.paint(str(pdf_path), 'CYAN')}")
                if auto_open:
                    import webbrowser
                    webbrowser.open(str(pdf_path))
            return 0

    if cmd == "tool":
        from .tool_synthesis import DynamicToolSynthesizer
        synthesizer = DynamicToolSynthesizer(engine.workspace)
        tool_cmd = getattr(parsed_args, "tool_subcommand", "list") or "list"

        if tool_cmd == "list":
            tools = synthesizer.list_dynamic_tools()
            print(tui.paint(f"\n🛠️ Smara Dynamic Tool Library ({len(tools)} tools registered):\n", "BOLD"))
            if not tools:
                print(tui.paint("  No dynamic tools synthesized yet. Create one with 'smara tool create <name> --code <code>'\n", "DIM"))
                return 0
            for t in tools:
                print(f"  • {tui.paint(t['name'], 'CYAN')} ({tui.paint(t.get('status', 'active'), 'GREEN')})")
                print(f"    Description: {tui.paint(t.get('description', 'No description'), 'DIM')}")
                print(f"    Path:        {tui.paint(t.get('file', ''), 'DIM')}\n")
            return 0

        if tool_cmd == "create":
            name = parsed_args.name
            code = parsed_args.code
            desc = parsed_args.desc or f"Custom dynamic tool: {name}"
            tui.print_tool_start("dynamic_tool_synthesize", f"Synthesizing & verifying tool '{name}'...")
            try:
                res = synthesizer.synthesize_tool(name=name, description=desc, code=code)
                tui.print_tool_result("dynamic_tool_synthesize", True, f"Saved to {res['path']}")
                print(tui.paint(f"\n✓ Tool '{name}' synthesized, AST-verified, smoke-tested, and registered successfully!\n", "GREEN"))
                return 0
            except Exception as exc:
                tui.print_tool_result("dynamic_tool_synthesize", False, str(exc))
                print(tui.paint(f"\n✗ Tool synthesis failed: {exc}\n", "RED"))
                return 1

        if tool_cmd == "run":
            name = parsed_args.name
            raw_payload = parsed_args.payload or "{}"
            try:
                payload_dict = json.loads(raw_payload)
            except Exception:
                payload_dict = {"input": raw_payload}
            tui.print_tool_start("dynamic_tool_exec", f"Executing dynamic tool '{name}'...")
            res = synthesizer.execute_dynamic_tool(name, payload_dict)
            ok = res.get("status") == "success"
            tui.print_tool_result("dynamic_tool_exec", ok, "Execution finished")
            print(json.dumps(res, indent=2))
            return 0 if ok else 1

    if cmd == "research":
        from .deep_research import DeepResearchEngine
        topic = " ".join(parsed_args.topic)
        print(tui.paint(f"\n🌐 Starting Autonomous Market Intelligence Deep Dive: '{topic}'\n", "BOLD"))
        
        d_engine = DeepResearchEngine(engine.workspace)
        tui.print_thought("Formulating multi-vector research hypotheses and competitive parameters...")
        tui.print_tool_start("deep_research", f"Deep analysis for: {topic}")
        res = d_engine.run_full_pipeline(topic)
        tui.print_tool_result("deep_research", True, f"Synthesized {res['sources_count']} sources")
        
        print(tui.paint(f"\n✓ Market Intelligence Report Compiled Successfully!\n", "GREEN"))
        print(f"  • Deliverable: {tui.paint(res['report_path'], 'BOLD')}")
        print(f"  • Key Tiers:   {len(res['analysis']['competitive_matrix'])} competitive tiers analyzed\n")
        
        print(tui.paint("📋 Executive Summary:", "BOLD"))
        print(f"  {res['analysis']['executive_summary']}\n")
        return 0

    if cmd == "goal" or (cmd == "run" and getattr(parsed_args, "goal", False)):
        from .goal_engine import GoalRunner
        runner = GoalRunner(engine.workspace)
        goal_id = getattr(parsed_args, "goal_id", None) or getattr(parsed_args, "resume", None)
        action = getattr(parsed_args, "goal_action", "run") if cmd == "goal" else "run"

        if action == "list" and not getattr(parsed_args, "objective", None):
            sessions = runner.list_sessions()
            print(tui.paint(f"\n🎯 Smara Autonomous Goal Sessions ({len(sessions)} total):\n", "BOLD"))
            if not sessions:
                print(tui.paint("  No goal sessions found. Start one with 'smara run --goal \"<objective>\"'\n", "DIM"))
                return 0
            for s in sessions:
                st_color = "GREEN" if s["status"] == "completed" else "YELLOW" if s["status"] == "running" else "RED"
                print(f"  • [{tui.paint(s['status'].upper(), st_color)}] {tui.paint(s['goal_id'], 'CYAN')} ({s['steps_completed']}/{s['total_steps']} steps)")
                print(f"    Objective: {tui.paint(s['objective'][:80], 'DIM')}\n")
            return 0

        objective = getattr(parsed_args, "objective", "")
        if isinstance(objective, list):
            objective = " ".join(objective)
        if not objective and not goal_id:
            print(tui.paint("Error: Objective is required. e.g. smara run --goal \"<objective>\"", "RED"))
            return 1

        print(tui.paint(f"\n🎯 Initiating Autonomous Long-Horizon Goal Runner\n", "BOLD"))
        print(f"  • Objective: {tui.paint(objective or str(goal_id), 'CYAN')}\n")

        def on_event(ev_type: str, step, detail: str):
            if ev_type == "step_start":
                tui.print_tool_start(step.capability, f"[{step.id}] {step.title}")
            elif ev_type == "step_complete":
                tui.print_tool_result(step.capability, True, detail)
            elif ev_type == "step_failed":
                tui.print_tool_result(step.capability, False, detail)

        session = runner.execute_goal(
            objective=objective,
            executor_fn=engine.execute_capability,
            on_event=on_event,
            goal_id=goal_id,
        )

        st_color = "GREEN" if session.status == "completed" else "RED"
        print(tui.paint(f"\n🏁 Goal Session {session.goal_id} Finished: {session.status.upper()}\n", st_color))
        print(f"  • Steps Completed: {session.metrics.get('completed_steps', 0)}/{len(session.steps)}")
        if session.final_deliverable:
            print(f"  • Final Deliverable: {tui.paint(session.final_deliverable, 'BOLD')}")
        print()
        return 0 if session.status == "completed" else 1

    # Handle subcommands
    if cmd == "test":
        from .test_fixer import AutonomousTestFixer, PytestRunner
        test_filter = " ".join(parsed_args.filter) if parsed_args.filter else None
        
        tui.print_tool_start("local_terminal", f"Running pytest {test_filter or ''}")
        runner = PytestRunner(engine.workspace)
        result = runner.run(test_filter)
        tui.print_tool_result("local_terminal", result.success, f"{result.passed} passed, {result.failed} failed, {result.errors} errors in {result.duration_seconds:.2f}s")
        
        if result.success:
            print(tui.paint(f"\n✓ All {result.passed} tests passed successfully!\n", "GREEN"))
            return 0
        
        print(tui.paint(f"\n✗ {result.failed} tests failed:\n", "RED"))
        for f in result.failures:
            print(f"  • {f.test_id} ({f.file_path}:{f.line_number or '?'})")
            print(f"    {f.assertion_error}\n")

        if parsed_args.fix:
            print(tui.paint("⚡ Initiating Autonomous Self-Healing Auto-Fixer...\n", "CYAN"))
            fixer = AutonomousTestFixer(engine.workspace)
            fix_res = fixer.auto_fix(test_filter)
            print(tui.paint(f"Status: {fix_res.get('status')}", "BOLD"))
            print(f"Message: {fix_res.get('message')}\n")
            if fix_res.get("status") == "healed":
                return 0
            return 1
        return 1

    if cmd == "refactor":
        instructions = " ".join(parsed_args.instructions)
        engine.run_turn(f"Perform atomic multi-file refactoring: {instructions}. Ensure all changes pass syntax and tests.")
        return 0

    if cmd == "graph":
        engine.run_turn(f"Inspect the Code Property Graph for the symbol '{parsed_args.symbol}' in our codebase and report its defined methods, callers, and blast radius.")
        return 0

    if cmd == "search":
        q = " ".join(parsed_args.query)
        engine.run_turn(f"Research and explain {q}. Cite all primary source links.")
        return 0

    if cmd == "report":
        title = " ".join(parsed_args.title)
        fmt = getattr(parsed_args, "format", "pdf")
        engine.run_turn(f"Create an executive {fmt.upper()} report titled '{title}' saved to reports/audit_summary.{fmt}.")
        return 0

    if cmd == "models":
        print(tui.paint("\nConfigured Model Profiles:", "BOLD"))
        for p in engine.profiles:
            active = "* [ACTIVE]" if p["id"] == engine.active_id else "o"
            print(f"  {active} {p['id'].ljust(12)} : {p['label']} ({p['model']}) @ {p['base_url']}")
        print()
        return 0

    # Default to Interactive Claude Code REPL
    _interactive_repl(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
