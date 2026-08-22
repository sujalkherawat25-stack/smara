"""Smara CLI: a thin client of the hosted Smara API, never a second agent brain."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


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
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="short direct conversation")
    ask.add_argument("message")
    ask.add_argument("--workspace", default="default")
    login = commands.add_parser("login", help="exchange a one-time Web pairing code for a CLI token")
    login.add_argument("code")
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
            elif args.command == "login":
                result = _request(client, "POST", "/v1/cli/device/exchange", json={"code": args.code})
                _save_token(result)
                if args.print_token:
                    print(result["access_token"])
                else:
                    print(f"Smara CLI login saved to {_token_path()}")
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
