"""Native Desktop metadata/vault management. No legacy execution engine imports."""
from __future__ import annotations
import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from .native_profiles import load_profiles, protect, read_object, resolve_credential, state_path, write_object
from .native_search import PROVIDERS, search_status

PROFILE_FIELDS = {"id", "label", "model", "provider", "base_url", "auth_header", "credential_name", "updated_at", "context_window"}


def settings_path():
    return state_path().parent / "native-settings.json"


def vault_path():
    return Path(os.getenv("SMARA_DESKTOP_CREDENTIALS", str(state_path().parent / "credentials.json")))


def folder(value):
    path = Path(str(value))
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Choose an existing absolute project folder")
    return str(path.resolve(strict=True))


def preferences():
    saved = read_object(settings_path())
    legacy = read_object(state_path().parent / "desktop-ui.json")
    state = read_object(state_path())
    projects = saved.get("projects")
    if projects is None:
        candidates = [legacy.get("workspace"), state.get("workspace"), *(legacy.get("allowed_roots") or state.get("allowed_roots") or [])]
        projects = []
        for candidate in candidates:
            if isinstance(candidate, str) and Path(candidate).is_absolute() and Path(candidate).is_dir():
                canonical = folder(candidate)
                if not any(os.path.normcase(p["workspace"]) == os.path.normcase(canonical) for p in projects):
                    projects.append({"workspace": canonical, "label": Path(canonical).name or canonical})
    if not isinstance(projects, list) or any(not isinstance(p, dict) or not isinstance(p.get("workspace"), str) or not isinstance(p.get("label"), str) for p in projects):
        raise ValueError("Native project settings are invalid; restore your settings backup")
    _, selected, _ = load_profiles()
    return {"projects": projects, "workspace": saved.get("workspace") or (projects[0]["workspace"] if projects else ""),
            "active_model": selected, "search_provider": saved.get("search_provider") or search_status(protected_only=True).get("provider") or "tavily"}


@contextlib.contextmanager
def management_lock():
    """Serialize metadata read/modify/write across Desktop and CLI processes."""
    path = settings_path().parent / "native-management.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def credential_summaries():
    return [{"name": name, "provider": str(item.get("provider") or "custom"), "updated_at": str(item.get("updated_at") or "")}
            for name, item in sorted(read_object(vault_path()).items()) if isinstance(item, dict)]


def put_credential(name, secret, provider):
    name = str(name).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", name):
        raise ValueError("Use an uppercase credential alias, such as TAVILY_API_KEY")
    if not isinstance(secret, str) or not secret.strip() or len(secret) > 16384:
        raise ValueError("Enter a credential value of 1..16384 characters")
    records = read_object(vault_path())
    records[name] = {"provider": str(provider)[:40], "protected": protect(secret.strip()), "updated_at": datetime.now(timezone.utc).isoformat()}
    write_object(vault_path(), records)


def bootstrap():
    profiles, _, _ = load_profiles()
    profiles = [{k: v for k, v in profile.items() if k in PROFILE_FIELDS} for profile in profiles]
    return {"preferences": preferences(), "profiles": profiles, "credentials": credential_summaries(), "search": search_status(protected_only=True)}


def manage(request):
    if not isinstance(request, dict) or not isinstance(request.get("operation"), str):
        raise ValueError("Invalid native management request")
    operation = request["operation"]
    with management_lock():
        if operation == "bootstrap":
            return bootstrap()
        if operation == "resolve_credential":
            # Internal native launch only. Not registered as a UI command.
            return {"secret": resolve_credential(str(request.get("name") or ""))}
        saved = read_object(settings_path())
        prefs = preferences()
        vault_before = None
        if operation == "add_project":
            workspace = folder(request.get("workspace") or "")
            label = str(request.get("label") or Path(workspace).name or workspace).strip()
            if not label or len(label) > 100:
                raise ValueError("Project name must be 1..100 characters")
            projects = prefs["projects"]
            if not any(os.path.normcase(p["workspace"]) == os.path.normcase(workspace) for p in projects):
                projects.append({"workspace": workspace, "label": label})
            saved.update({"projects": projects, "workspace": workspace})
        elif operation == "select_project":
            workspace = folder(request.get("workspace") or "")
            if not any(os.path.normcase(p["workspace"]) == os.path.normcase(workspace) for p in prefs["projects"]):
                raise ValueError("Add this folder as a project before using it")
            saved.update({"projects": prefs["projects"], "workspace": workspace})
        elif operation == "select_model":
            profiles, _, _ = load_profiles()
            identity = str(request.get("id") or "")
            if not any(p.get("id") == identity for p in profiles):
                raise ValueError("Choose a configured model")
            saved["active_model"] = identity
        elif operation == "select_search":
            provider = request.get("provider")
            if provider not in PROVIDERS:
                raise ValueError("Select Tavily, Exa, Serper or Brave")
            saved["search_provider"] = provider
        elif operation == "save_credential":
            provider = str(request.get("provider") or "custom").lower()
            name = str(request.get("name") or "")
            if provider in PROVIDERS and name.upper() != PROVIDERS[provider][1]:
                raise ValueError("Search key alias does not match the selected provider")
            vault_before = read_object(vault_path())
            put_credential(name, request.get("secret"), provider)
            if provider in PROVIDERS:
                saved["search_provider"] = provider
        elif operation == "delete_credential":
            name = str(request.get("name") or "")
            records = read_object(vault_path())
            records.pop(name, None)
            write_object(vault_path(), records)
        elif operation == "save_model":
            profile = request.get("profile")
            if not isinstance(profile, dict):
                raise ValueError("Invalid model profile")
            identity = str(profile.get("id") or "").lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,48}", identity):
                raise ValueError("Model id must be 1..48 letters, digits, hyphens or underscores")
            base = str(profile.get("base_url") or "").strip().rstrip("/")
            parsed = urlsplit(base)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Use an HTTP(S) model endpoint without credentials, query or fragment")
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Remote model endpoints must use HTTPS")
            header = profile.get("auth_header") or "authorization"
            if header not in {"authorization", "api-subscription-key"}:
                raise ValueError("Unsupported model authentication header")
            for field, limit in (("label", 80), ("model", 160), ("provider", 40)):
                if not isinstance(profile.get(field), str) or not profile[field].strip() or len(profile[field]) > limit:
                    raise ValueError("Enter a valid model name, label and provider")
            profiles, _, _ = load_profiles()
            previous = next((p for p in profiles if p.get("id") == identity), None)
            alias = str((previous or {}).get("credential_name") or ("SMARA_MODEL_" + identity.upper().replace("-", "_") + "_API_KEY"))
            context_window = profile.get("context_window", (previous or {}).get("context_window"))
            if context_window is not None and (type(context_window) is not int or not 4096 <= context_window <= 10_000_000):
                raise ValueError("Model context window must be an integer from 4096 to 10000000 tokens")
            secret = profile.get("api_key") or ""
            if secret:
                alias = alias.strip().upper()  # put_credential stores this exact alias.
                vault_before = read_object(vault_path())
                put_credential(alias, secret, "model:" + profile["provider"])
            elif not previous:
                raise ValueError("Enter a model API key (or a local-server placeholder) for a new profile")
            # Never copy arbitrary input fields, especially api_key, into metadata.
            clean = {field: profile[field].strip() for field in ("label", "model", "provider")}
            clean.update({"id": identity, "base_url": base, "auth_header": header, "credential_name": alias, "updated_at": datetime.now(timezone.utc).isoformat()})
            if context_window is not None:
                clean["context_window"] = context_window
            saved["model_profiles"] = [{k: v for k, v in p.items() if k in PROFILE_FIELDS} for p in profiles if p.get("id") != identity] + [clean]
        else:
            raise ValueError("Unsupported native management operation; legacy execution is unavailable")
        if operation != "delete_credential":
            try:
                write_object(settings_path(), saved)
            except OSError:
                if vault_before is not None:
                    # Do not leave a replaced model key behind when metadata
                    # cannot be saved. Existing protected records stay intact.
                    write_object(vault_path(), vault_before)
                raise
        return bootstrap()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--native-app-server"]:
        from .native_runtime import serve_bootstrap
        return serve_bootstrap()
    if args[:1] == ["--native-tools"]:
        from .native_tools import main as serve
        return serve(args[1:])
    if args[:1] == ["--native-browser"]:
        from .native_browser import main as serve
        return serve(args[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-manage", action="store_true")
    parsed = parser.parse_args(args)
    if not parsed.native_manage:
        parser.error("Select --native-manage; no legacy executor is available")
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        request = json.loads(sys.stdin.read(131073))
        result = manage(request)
        print(json.dumps({"result": result}, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        # No raw OS/provider errors or request bodies at this boundary.
        detail = str(exc) if isinstance(exc, ValueError) else "Native settings or credential vault is unavailable; check the local settings backup"
        print(json.dumps({"error": detail}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
