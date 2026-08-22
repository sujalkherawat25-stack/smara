"""Smara CLI: a thin client of the hosted Smara API, never a second agent brain."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx


def _client(args: argparse.Namespace) -> httpx.Client:
    headers: dict[str, str] = {"Accept": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
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


def _watch_stream(client: httpx.Client, task_id: str) -> None:
    with client.stream("GET", f"/v1/tasks/{task_id}/events/stream", headers={"Accept": "text/event-stream"}) as response:
        if response.status_code >= 400:
            raise RuntimeError(f"{response.status_code}: event stream unavailable")
        event_name = "task_update"
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                _print({"event": event_name, "data": payload})
                if event_name == "done":
                    return


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with _client(args) as client:
            if args.command == "ask":
                _print(_request(client, "POST", "/v1/chat", json={"message": args.message, "workspace_id": args.workspace}))
            elif args.command == "login":
                result = _request(client, "POST", "/v1/cli/device/exchange", json={"code": args.code})
                print(result["access_token"])
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
