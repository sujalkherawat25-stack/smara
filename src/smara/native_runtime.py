"""Launch Smara's source-built runtime; never fall back to an installed Codex.

This module owns process/transport setup only. The copied Rust core owns the
reason-act loop, sessions, approvals, sandbox, workers and recovery.
"""
from __future__ import annotations

import contextlib
import json
from importlib.resources import files
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

from .native_provider import ChatEndpoint, ResponsesAdapter


def source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def source_manifest() -> dict:
    for candidate in (Path(sys.executable).parent / "native/UPSTREAM.json", source_root() / "native/UPSTREAM.json"):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(files("smara").joinpath("native_assets/UPSTREAM.json").read_text(encoding="utf-8"))


def native_binary(explicit: str | None = None) -> Path:
    selected = explicit or os.getenv("SMARA_NATIVE_BINARY")
    candidates = ([Path(selected)] if selected else [
        Path(sys.executable).parent / "native" / "smara-native.exe",
        source_root() / "native" / "dist" / ("smara-native.exe" if os.name == "nt" else "smara-native"),
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Smara's copied native runtime has not been built. Run scripts/build-smara-native.ps1; no legacy or installed-Codex fallback is used.")


def launch_options(adapter: ResponsesAdapter, *, home: Path, workspace: Path, tools_enabled: bool = False) -> tuple[list[str], dict[str, str]]:
    if not workspace.is_dir():
        raise ValueError("Workspace must be an existing directory")
    home.mkdir(parents=True, exist_ok=True)
    settings = {
        "model_provider": "smara_chat_adapter",
        "model": adapter.endpoint.model,
        "model_providers.smara_chat_adapter.name": "Smara configured model",
        "model_providers.smara_chat_adapter.base_url": adapter.base_url,
        "model_providers.smara_chat_adapter.env_key": "SMARA_NATIVE_WIRE_TOKEN",
        "model_providers.smara_chat_adapter.wire_api": "responses",
        "model_providers.smara_chat_adapter.requires_openai_auth": False,
        "model_providers.smara_chat_adapter.supports_websockets": False,
        "approval_policy": "on-request",
        "sandbox_mode": "workspace-write",
        "web_search": "disabled",
        "analytics.enabled": False,
        "check_for_update_on_startup": False,
        "shell_environment_policy.inherit": "core",
        # Smara has no OpenAI-account/plugin-marketplace integration. Avoid
        # background catalog clones and inherited app/plugin authorization.
        "features.plugins": False,
        "features.apps": False,
    }
    if os.name == "nt":
        settings["windows.sandbox"] = "unelevated"
    if adapter.endpoint.effective_context_window:
        settings["model_context_window"] = adapter.endpoint.effective_context_window
        settings["model_auto_compact_token_limit"] = int(adapter.endpoint.effective_context_window * .8)
    if tools_enabled:
        tool_args = (["--native-tools"] if getattr(sys, "frozen", False) else ["-m", "smara.native_tools"])
        tool_args += ["--workspace", str(workspace.resolve())]
        settings.update({
            "mcp_servers.smara_readers.command": sys.executable,
            "mcp_servers.smara_readers.args": tool_args,
            "mcp_servers.smara_readers.env.PYTHONIOENCODING": "utf-8",
            "mcp_servers.smara_readers.env.PYTHONUTF8": "1",
            "mcp_servers.smara_readers.required": True,
            "mcp_servers.smara_readers.startup_timeout_sec": 30,
            "mcp_servers.smara_readers.tool_timeout_sec": 45,
        })
    argv = [part for key, value in settings.items() for part in ("-c", key + "=" + json.dumps(value))]
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home.resolve())
    environment["SMARA_NATIVE_WIRE_TOKEN"] = adapter.token
    # Never give the executor the configured provider key. The model transport
    # holds it in memory and rejects unauthenticated loopback requests.
    model_secrets = {"SMARA_LLM_API_KEY", "SARVAM_API_KEY", "OPENAI_API_KEY", "SMARA_LLM_PROFILES",
        "GROK_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "MISTRAL_API_KEY"}
    for key in list(environment):
        if key in model_secrets or key.startswith("SMARA_MODEL_"):
            environment.pop(key)
    return argv, environment


def runtime_home() -> Path:
    explicit = os.getenv("SMARA_NATIVE_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    app_data = Path(os.getenv("APPDATA", str(Path.home() / ".config")))
    return app_data / "Smara" / "native-runtime"


def active_profile(profiles: list[dict[str, Any]], active_id: str) -> dict[str, Any]:
    matches = [profile for profile in profiles if profile.get("id") == active_id]
    if len(matches) != 1:
        raise RuntimeError("Select one valid model profile in Smara Settings; native execution will not silently substitute a provider")
    return matches[0]


def print_smara_help() -> None:
    print("\nSmara integration commands (execution remains owned by the native engine):")
    print("  smara source-status                Show offline source/binary provenance")
    print("  smara --smara-tools [native args]   Opt in to public research/local-memory MCP readers")
    print("  smara schedule --help              Manage paused-by-default, bounded read-only jobs")
    print("  smara tools-serve --workspace DIR  Serve read-only MCP primitives on stdio")
    print("  smara legacy [args]                Explicit old-engine access (not in portable CLI)")
    print("\nThis fork uses its own source-built binary, not an installed Codex. Integration")
    print("readers do not provide interactive browser/computer actions or company-write automation.")


def configure_protocol_stdio(input_stream, output_stream) -> None:
    # Frozen Python can ignore PYTHONIOENCODING and select a Windows codepage.
    # Native JSON-RPC always carries UTF-8 paths, messages and streamed output.
    for stream in (input_stream, output_stream):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")


def serve_bootstrap(input_stream=None, output_stream=None) -> int:
    """First stdin line is private launch configuration; subsequent lines are RPC.

    No session IDs, tool results or approvals are remapped. Native JSON-RPC is
    passed end-to-end so the Rust runtime remains the only session authority.
    """
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    configure_protocol_stdio(input_stream, output_stream)
    config = json.loads(input_stream.readline())
    binary = native_binary(config.get("binary"))
    workspace = Path(config["workspace"]).resolve()
    endpoint = ChatEndpoint(config["base_url"], config["model"], config.get("api_key", ""), config.get("auth_header", "authorization"), config.get("context_window"))
    home = Path(config["home"]) if config.get("home") else runtime_home()
    with ResponsesAdapter(endpoint) as adapter:
        options, environment = launch_options(adapter, home=home, workspace=workspace, tools_enabled=config.get("tools_enabled") is True)
        process = subprocess.Popen([str(binary), *options, "app-server", "--listen", "stdio://"],
            cwd=workspace, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def forward_input():
            try:
                for line in input_stream:
                    if json.loads(line).get("method") == "smara/shutdown":
                        # EOF enters the copied app-server's connection cleanup:
                        # it cancels threads and closes managed tool processes.
                        # TerminateProcess skips that cleanup on Windows.
                        return
                    process.stdin.write(line)
                    process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                with contextlib.suppress(OSError):
                    process.stdin.close()
                try:
                    process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    process.terminate()
        def drain_errors():
            # The Rust runtime persists its own diagnostics. Do not forward
            # stderr to Desktop or logs: it can contain provider/private data.
            for _line in process.stderr:
                pass
        threading.Thread(target=forward_input, daemon=True).start()
        threading.Thread(target=drain_errors, daemon=True).start()
        try:
            for line in process.stdout:
                output_stream.write(line)
                output_stream.flush()
            return process.wait()
        finally:
            if process.poll() is None:
                with contextlib.suppress(OSError):
                    process.stdin.close()
                try:
                    process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["schedule"]:
        from .native_schedule import main as schedule_main
        try:
            return schedule_main(args[1:])
        except (RuntimeError, ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args and args[0] == "tools-serve":
        from .native_tools import main as tools_main
        return tools_main(args[1:])
    tools_enabled = "--smara-tools" in args
    if tools_enabled:
        args.remove("--smara-tools")
    if args and args[0] == "legacy":
        if getattr(sys, "frozen", False):
            print("The native portable CLI does not bundle the legacy engine. Use smara-legacy from the Python package for migration tools.", file=sys.stderr)
            return 1
        from .cli import main as legacy_main
        return legacy_main(args[1:])
    if args == ["source-status"]:
        manifest = source_manifest()
        try:
            manifest["binary"] = str(native_binary())
            manifest["built"] = True
        except RuntimeError:
            manifest["built"] = False
        print(json.dumps(manifest, indent=2))
        return 0
    try:
        binary = native_binary()
        # Help/version are offline and do not need a configured provider.
        if args in (["--help"], ["-h"], ["--version"], ["-V"]):
            environment = dict(os.environ)
            home = runtime_home()
            home.mkdir(parents=True, exist_ok=True)
            environment["CODEX_HOME"] = str(home)
            result = subprocess.call([str(binary), *args], env=environment)
            if result == 0 and args in (["--help"], ["-h"]):
                print_smara_help()
            return result
        from .native_profiles import load_profiles, resolve_profile_key
        profiles, active_id, credentials = load_profiles()
        profile = active_profile(profiles, active_id)
        endpoint = ChatEndpoint(profile["base_url"], profile["model"], resolve_profile_key(profile, credentials), profile.get("auth_header", "authorization"), profile.get("context_window"))
        with ResponsesAdapter(endpoint) as adapter:
            options, environment = launch_options(adapter, home=runtime_home(), workspace=Path.cwd(), tools_enabled=tools_enabled)
            # The adapter belongs to this invocation. A detached shared daemon
            # must not retain its now-dead URL/token after the CLI exits.
            return subprocess.call([str(binary), *options, "--no-daemon", *args], env=environment)
    except (RuntimeError, ValueError, KeyError, IndexError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
