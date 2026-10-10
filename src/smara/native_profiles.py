"""Native profile/vault reads without importing the retired agent engines."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import re
import tempfile


def state_path() -> Path:
    if os.getenv("SMARA_DESKTOP_STATE"):
        return Path(os.environ["SMARA_DESKTOP_STATE"])
    return Path(os.getenv("APPDATA", str(Path.home() / ".config"))) / "Smara/desktop.json"


def read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Native profile/vault file could not be read; repair it in Settings") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Native profile/vault file must be an object")
    return value


def load_profiles(path: Path | None = None):
    path = path or state_path()
    state = read_object(path)
    preferences = read_object(path.parent / "desktop-ui.json")
    native = read_object(path.parent / "native-settings.json")
    profiles = native.get("model_profiles", state.get("model_profiles") or preferences.get("local_model_profiles") or [])
    selected = str(native.get("active_model", preferences.get("model_profile") or state.get("active_model") or "")).removeprefix("local:")
    if not isinstance(profiles, list) or any(not isinstance(item, dict) for item in profiles):
        raise RuntimeError("Invalid native model profiles")
    vault_path = Path(os.getenv("SMARA_DESKTOP_CREDENTIALS", str(path.parent / "credentials.json")))
    return profiles, selected, read_object(vault_path)


def unprotect(value: str) -> str:
    if os.name != "nt":
        return value
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    raw = base64.b64decode(value, validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    clear = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(clear)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(clear.pbData, clear.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(clear.pbData)


def resolve_credential(name: str, credentials: dict | None = None, *, allow_environment: bool = True) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", name):
        return ""
    if allow_environment and os.getenv(name):
        return os.environ[name]
    if credentials is None:
        path = Path(os.getenv("SMARA_DESKTOP_CREDENTIALS", str(state_path().parent / "credentials.json")))
        credentials = read_object(path)
    for key in dict.fromkeys((name, name.upper(), name.lower())):
        value = credentials.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and isinstance(value.get("protected"), str):
            try:
                clear = unprotect(value["protected"])
                if clear:
                    return clear
            except (OSError, ValueError, UnicodeError):
                continue  # One stale alias must not mask a readable alias.
    return ""


def write_object(path: Path, value: dict) -> None:
    """Atomic private metadata/vault replacement; never truncate on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
    try:
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def protect(value: str) -> str:
    """DPAPI protection, compatible with the existing Windows vault."""
    if os.name != "nt":
        raise RuntimeError("Credential storage currently requires Windows DPAPI; use environment keys on other systems")
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    encrypted = Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(encrypted)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(encrypted.pbData, encrypted.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(encrypted.pbData)


def resolve_profile_key(profile: dict, credentials: dict) -> str:
    identity = str(profile.get("id", ""))
    provider = str(profile.get("provider", "")).lower()
    aliases = [profile.get("credential_name"), f"SMARA_MODEL_{identity.upper()}_API_KEY", f"model_api_key_{identity}",
               f"{identity.upper()}_API_KEY", f"{identity}_api_key"]
    if identity.startswith("sarvam") or provider.startswith("sarvam"):
        aliases += ["SMARA_MODEL_SARVAM_API_KEY", "SARVAM_API_KEY"]
    if identity == "grok":
        aliases += ["GROK_API_KEY", "XAI_API_KEY"]
    for alias in aliases:
        if isinstance(alias, str):
            value = resolve_credential(alias, credentials)
            if value:
                return value
    return "ollama-local" if identity == "ollama" else ""
