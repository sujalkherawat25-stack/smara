"""Smara CLI: a thin client of the hosted Smara API, never a second agent brain."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

import httpx


class _TerminalUI:
    """Small, dependency-free terminal shell for the hosted agent.

    It deliberately uses only ANSI styling so the CLI stays fast to install
    and works in PowerShell, Windows Terminal, and ordinary Unix terminals.
    ``plain`` disables styling while keeping the same readable layout.
    """

    _colors = {
        "blue": "\x1b[38;5;111m",
        "cyan": "\x1b[38;5;80m",
        "green": "\x1b[38;5;114m",
        "muted": "\x1b[38;5;245m",
        "red": "\x1b[38;5;210m",
        "yellow": "\x1b[38;5;221m",
        "reset": "\x1b[0m",
        "bold": "\x1b[1m",
    }

    def __init__(self, *, plain: bool = False):
        self.enabled = bool(sys.stdout.isatty() and not plain)
        encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
        self.glyph = {
            "brand": "✦" if "utf" in encoding else ">",
            "online": "●" if "utf" in encoding else "*",
            "offline": "○" if "utf" in encoding else "o",
            "prompt": "❯" if "utf" in encoding else ">",
            "phase": "·" if "utf" in encoding else "-",
            "status": "↳" if "utf" in encoding else ">",
            "tool": "⚙" if "utf" in encoding else "+",
            "ok": "✓" if "utf" in encoding else "OK",
            "error": "✗" if "utf" in encoding else "!",
        }

    def paint(self, text: str, color: str) -> str:
        if not self.enabled:
            return text
        return f"{self._colors.get(color, '')}{text}{self._colors['reset']}"

    def banner(self, *, api_url: str, session: str, workspace: str, authenticated: bool) -> None:
        host = urlparse(api_url).netloc or api_url
        auth = self.paint(f"{self.glyph['online']} connected", "green") if authenticated else self.paint(f"{self.glyph['offline']} not logged in", "yellow")
        print()
        print(self.paint(f"  {self.glyph['brand']}  SMARA", "bold"))
        print(f"  {auth}  {self.paint(host, 'muted')}")
        print(f"  {self.paint('session', 'muted')} {self.paint(session, 'cyan')}  {self.glyph['phase']}  {self.paint('workspace', 'muted')} {self.paint(workspace, 'cyan')}")
        print(f"  {self.paint('Type /help for commands | Ctrl+C cancels a turn', 'muted')}")
        print()

    def prompt(self) -> str:
        return self.paint(f"you {self.glyph['prompt']} ", "blue")

    def assistant(self) -> None:
        print(self.paint(f"\nsmara {self.glyph['prompt']} ", "green"), end="", flush=True)

    def phase(self, name: str) -> None:
        print(f"  {self.paint(self.glyph['phase'], 'blue')} {self.paint(name, 'muted')}")

    def status(self, label: str) -> None:
        print(f"  {self.paint(self.glyph['status'], 'muted')} {self.paint(label, 'muted')}")

    def tool_call(self, name: str) -> None:
        print(f"  {self.paint(self.glyph['tool'], 'yellow')} {self.paint(name, 'yellow')}")

    def tool_result(self, name: str, ok: bool) -> None:
        mark = self.glyph["ok"] if ok else self.glyph["error"]
        color = "green" if ok else "red"
        print(f"  {self.paint(mark, color)} {self.paint(name, color)}")

    def done(self, *, total_ms: int | None = None, tools_used: int | None = None) -> None:
        parts = []
        if total_ms is not None:
            parts.append(f"{total_ms} ms")
        if tools_used:
            parts.append(f"{tools_used} tool{'s' if tools_used != 1 else ''}")
        if parts:
            separator = f" {self.glyph['phase']} "
            print(f"\n  {self.paint(self.glyph['phase'], 'muted')} {self.paint(separator.join(parts), 'muted')}")

    def error(self, message: str) -> None:
        print(f"\n  {self.paint(self.glyph['error'], 'red')} {self.paint(message, 'red')}", file=sys.stderr)

    def help(self) -> None:
        print(self.paint("\nSmara commands", "bold"))
        print("  /help                 Show this help")
        print("  /sessions             List saved sessions")
        print("  /new NAME             Start or switch to a session")
        print("  /workspace NAME       Change the active workspace")
        print("  /status               Check hosted connection and tools")
        print("  /tools                List available agent tools")
        print("  /tasks                List your durable tasks")
        print("  /exit                 Leave chat")
        print("\n  Ctrl+C cancels the current turn; Ctrl+D exits.\n")


def _client(args: argparse.Namespace) -> httpx.Client:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = args.token or _load_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if args.dev_account:
        headers["X-Smara-Account-Id"] = args.dev_account
    return httpx.Client(base_url=args.api.rstrip("/"), headers=headers, timeout=30)


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{response.status_code}: {detail}")
    return response.json() if response.content else {"ok": True}


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


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


def _sessions_path() -> Path:
    configured = os.getenv("SMARA_SESSION_FILE")
    if configured:
        return Path(configured)
    root = Path(os.getenv("APPDATA", Path.home() / ".config")) / "Smara"
    return root / "sessions.json"


def _load_sessions() -> dict[str, dict[str, str]]:
    try:
        value = json.loads(_sessions_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_sessions(sessions: dict[str, dict[str, str]]) -> None:
    path = _sessions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sessions, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def _session_id(name: str, sessions: dict[str, dict[str, str]]) -> str:
    import hashlib
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:20]
    sessions.setdefault(name, {"conversation_id": f"cli_{digest}"})
    _save_sessions(sessions)
    return str(sessions[name]["conversation_id"])


def _stream_chat(
    client: httpx.Client,
    *,
    message: str,
    workspace: str,
    conversation_id: str,
    model_profile: str | None = None,
    ui: _TerminalUI | None = None,
) -> str:
    """Print a safe streaming turn and return the conversation id.

    The API emits bounded status/tool/token events. We intentionally render
    tool previews and final text, never model chain-of-thought or credentials.
    """
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
                # Smara's API uses the compact SSE form (`data: {"type":
                # "token", ...}`) while some compatible gateways also send
                # an explicit `event:` line.  Prefer the payload type so the
                # CLI cannot silently drop a valid response when the event
                # line is omitted.
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


def _interactive_chat(client: httpx.Client, args: argparse.Namespace) -> None:
    sessions = _load_sessions()
    conversation_id = _session_id(args.session, sessions)
    ui = _TerminalUI(plain=args.plain)
    ui.banner(api_url=args.api, session=args.session, workspace=args.workspace, authenticated=bool(client.headers.get("Authorization")))
    while True:
        try:
            message = input(ui.prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message in {"/exit", "/quit"}:
            return
        if message == "/help":
            ui.help()
            continue
        if message == "/sessions":
            for name in sorted(sessions):
                print(f" {'*' if name == args.session else ' '} {name}")
            if not sessions:
                print(" (no saved sessions)")
            continue
        if message.startswith("/new"):
            name = message[4:].strip() or "default"
            args.session = name
            conversation_id = _session_id(name, sessions)
            print(f"Switched to {ui.paint(name, 'cyan')}.")
            continue
        if message.startswith("/workspace"):
            workspace = message[len("/workspace"):].strip()
            if workspace:
                args.workspace = workspace
                print(f"Workspace set to {ui.paint(args.workspace, 'cyan')}.")
            else:
                print(f"Active workspace: {ui.paint(args.workspace, 'cyan')}")
            continue
        if message == "/status":
            try:
                _request(client, "GET", "/health")
                tools = _request(client, "GET", "/v1/tools")
                if isinstance(tools, list):
                    count = len(tools)
                elif isinstance(tools, dict) and isinstance(tools.get("tools"), list):
                    count = len(tools["tools"])
                else:
                    count = "available"
                print(f" {ui.paint(ui.glyph['ok'], 'green')} hosted API healthy | {count} tools")
            except (RuntimeError, httpx.HTTPError) as exc:
                ui.error(str(exc))
            continue
        if message == "/tools":
            _print(_request(client, "GET", "/v1/tools"))
            continue
        if message == "/tasks":
            _print(_request(client, "GET", "/v1/tasks"))
            continue
        ui.assistant()
        try:
            _stream_chat(client, message=message, workspace=args.workspace, conversation_id=conversation_id, model_profile=args.model_profile, ui=ui)
        except KeyboardInterrupt:
            print()
            ui.error("Turn cancelled. Your session is still available.")
        except (RuntimeError, httpx.HTTPError) as exc:
            ui.error(str(exc))


def _browser_login(client: httpx.Client, args: argparse.Namespace) -> None:
    """Run a short-lived browser device flow; no bearer is shown in output."""
    request = _request(client, "GET", "/v1/cli/device/request", params={"name": "Smara CLI"})
    device_code = request["device_code"]
    configured_url = args.auth_url or os.getenv("SMARA_CLI_AUTH_URL", "")
    if configured_url:
        auth_url = configured_url.replace("{device_code}", quote(device_code, safe=""))
    else:
        auth_url = f"{args.api.rstrip('/')}/app/?cli_device={quote(device_code, safe='')}"
    print("Opening Smara in your browser. Approve this CLI device to continue…", file=sys.stderr)
    if not webbrowser.open(auth_url):
        print(f"Open this URL to approve the CLI device:\n{auth_url}", file=sys.stderr)
    deadline = time.monotonic() + max(30, args.timeout)
    interval = max(1, int(request.get("interval", 2)))
    while time.monotonic() < deadline:
        result = _request(client, "GET", "/v1/cli/device/poll", params={"device_code": device_code})
        status = result.get("status")
        if status == "approved":
            _save_token(result)
            print(f"Smara CLI login saved to {_token_path()}")
            return
        if status in {"expired", "used"}:
            raise RuntimeError("Smara CLI device request expired or was already used.")
        time.sleep(interval)
    raise RuntimeError("Timed out waiting for browser approval. Run `smara login` again.")


def _watch_stream(client: httpx.Client, task_id: str) -> None:
    last_event_id = ""
    for attempt in range(4):
        stream_headers = {"Accept": "text/event-stream"}
        if last_event_id:
            stream_headers["Last-Event-ID"] = last_event_id
        try:
            with client.stream("GET", f"/v1/tasks/{task_id}/events/stream", headers=stream_headers) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"{response.status_code}: event stream unavailable")
                event_name = "task_update"
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("id: "):
                        last_event_id = line.removeprefix("id: ")
                    elif line.startswith("event: "):
                        event_name = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        payload = json.loads(line.removeprefix("data: "))
                        _print({"event": event_name, "data": payload})
                        if event_name == "done":
                            return
            if attempt == 3:
                raise RuntimeError("event stream disconnected after four attempts")
            print("smara: event stream disconnected; resuming…", file=sys.stderr)
            time.sleep(1)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            if attempt == 3:
                raise RuntimeError("event stream disconnected after four attempts") from exc
            print("smara: event stream unavailable; retrying…", file=sys.stderr)
            time.sleep(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smara", description="Smara task and agent client")
    parser.add_argument("--api", default=os.getenv("SMARA_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--token", default=os.getenv("SMARA_TOKEN", ""), help="Smara bearer token")
    parser.add_argument("--dev-account", default=os.getenv("SMARA_DEV_ACCOUNT", ""), help="development only")
    parser.add_argument("--plain", action="store_true", help="disable terminal colors and symbols")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="short direct conversation")
    ask.add_argument("message")
    ask.add_argument("--workspace", default="default")
    chat = commands.add_parser("chat", help="interactive streaming chat with resumable local session names")
    chat.add_argument("message", nargs="?")
    chat.add_argument("-q", "--query", dest="query")
    chat.add_argument("--session", default="default")
    chat.add_argument("--workspace", default="default")
    chat.add_argument("--model-profile", default=None, help="operator-configured model profile")
    chat.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="disable terminal colors and symbols")
    login = commands.add_parser("login", help="approve this CLI in Smara Web (or exchange a legacy code)")
    login.add_argument("code", nargs="?", help="legacy one-time pairing code")
    login.add_argument("--auth-url", help="browser authorization URL template containing {device_code}")
    login.add_argument("--timeout", type=int, default=300, help="seconds to wait for browser approval")
    login.add_argument("--print-token", action="store_true", help="print the bearer token instead of only saving it")
    commands.add_parser("logout", help="remove the locally saved CLI token")
    run = commands.add_parser("run", help="create a durable approval-gated task")
    run.add_argument("objective")
    run.add_argument("--title", default="Smara task")
    run.add_argument("--workspace", default="default")
    run.add_argument("--no-approval", action="store_true")
    research = commands.add_parser("research", help="create a cited research task")
    research.add_argument("question")
    research.add_argument("--title", default="Smara research")
    research.add_argument("--workspace", default="default")
    research.add_argument("--source", action="append", default=[], help="explicit source URL; repeat as needed")
    tasks = commands.add_parser("tasks", help="list durable tasks")
    tasks_commands = tasks.add_subparsers(dest="tasks_command")
    tasks_commands.add_parser("list", help="list durable tasks")
    task = commands.add_parser("task", help="inspect or control one task")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    for name in ("show", "watch", "cancel"):
        command = task_commands.add_parser(name)
        command.add_argument("task_id")
    evidence = task_commands.add_parser("evidence", help="show a research task's evidence ledger")
    evidence.add_argument("task_id")
    approval = task_commands.add_parser("approve")
    approval.add_argument("task_id")
    approval.add_argument("--note", default="Approved from Smara CLI")
    deny = task_commands.add_parser("deny")
    deny.add_argument("task_id")
    deny.add_argument("--note", default="Denied from Smara CLI")
    commands.add_parser("tools", help="list safe tools available to the agent")
    commands.add_parser("plugins", help="list enabled built-in and declared plugin descriptors")
    tool = commands.add_parser("tool", help="invoke one safe read-only tool")
    tool.add_argument("name")
    tool.add_argument("--arguments", default="{}", help="JSON object of tool arguments")
    tool.add_argument("--workspace", default="default")
    desktop = commands.add_parser("desktop", help="pair or inspect a local desktop executor")
    desktop_commands = desktop.add_subparsers(dest="desktop_command", required=True)
    desktop_commands.add_parser("list")
    pair = desktop_commands.add_parser("pair")
    pair.add_argument("--name", default="Smara desktop")
    pair.add_argument("--capability", action="append", dest="capabilities", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with _client(args) as client:
            if args.command == "ask":
                _print(_request(client, "POST", "/v1/chat", json={"message": args.message, "workspace_id": args.workspace}))
            elif args.command == "chat":
                message = args.query or args.message
                if message:
                    sessions = _load_sessions()
                    _stream_chat(client, message=message, workspace=args.workspace, conversation_id=_session_id(args.session, sessions), model_profile=args.model_profile)
                else:
                    _interactive_chat(client, args)
            elif args.command == "login":
                if args.code:
                    result = _request(client, "POST", "/v1/cli/device/exchange", json={"code": args.code})
                    _save_token(result)
                    if args.print_token:
                        print(result["access_token"])
                    else:
                        print(f"Smara CLI login saved to {_token_path()}")
                else:
                    if args.print_token:
                        raise RuntimeError("--print-token is only available with a legacy pairing code.")
                    _browser_login(client, args)
            elif args.command == "logout":
                _clear_token()
                print("Smara CLI token removed.")
            elif args.command == "run":
                _print(_request(client, "POST", "/v1/tasks", json={
                    "title": args.title,
                    "objective": args.objective,
                    "workspace_id": args.workspace,
                    "requires_approval": not args.no_approval,
                    "steps": [{"name": "agent.execute"}],
                }))
            elif args.command == "research":
                _print(_request(client, "POST", "/v1/research", json={
                    "title": args.title,
                    "question": args.question,
                    "workspace_id": args.workspace,
                    "sources": args.source,
                }))
            elif args.command == "tasks":
                _print(_request(client, "GET", "/v1/tasks"))
            elif args.command == "tools":
                _print(_request(client, "GET", "/v1/tools"))
            elif args.command == "plugins":
                _print(_request(client, "GET", "/v1/plugins"))
            elif args.command == "tool":
                try:
                    arguments = json.loads(args.arguments)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("--arguments must be a JSON object") from exc
                if not isinstance(arguments, dict):
                    raise RuntimeError("--arguments must be a JSON object")
                _print(_request(client, "POST", f"/v1/tools/{args.name}", json={"arguments": arguments, "workspace_id": args.workspace}))
            elif args.command == "desktop" and args.desktop_command == "list":
                _print(_request(client, "GET", "/v1/executors"))
            elif args.command == "desktop" and args.desktop_command == "pair":
                _print(_request(client, "POST", "/v1/executors/pairings", json={"name": args.name, "capabilities": args.capabilities or ["local_file_read"]}))
            elif args.task_command == "show":
                result = {
                    "task": _request(client, "GET", f"/v1/tasks/{args.task_id}"),
                    "steps": _request(client, "GET", f"/v1/tasks/{args.task_id}/steps"),
                    "events": _request(client, "GET", f"/v1/tasks/{args.task_id}/events"),
                }
                try: result["evidence"] = _request(client, "GET", f"/v1/research/{args.task_id}/evidence")
                except RuntimeError: pass
                _print(result)
            elif args.task_command == "evidence":
                _print(_request(client, "GET", f"/v1/research/{args.task_id}/evidence"))
            elif args.task_command == "watch":
                _watch_stream(client, args.task_id)
            elif args.task_command in {"approve", "deny"}:
                _print(_request(client, "POST", f"/v1/tasks/{args.task_id}/approval", json={
                    "approved": args.task_command == "approve", "note": args.note,
                }))
            elif args.task_command == "cancel":
                _print(_request(client, "POST", f"/v1/tasks/{args.task_id}/cancel"))
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"smara: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
