"""Native profile/vault reads without importing the retired agent engines."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import re


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
    profiles = state.get("model_profiles") or preferences.get("local_model_profiles") or []
    selected = str(preferences.get("model_profile") or state.get("active_model") or "").removeprefix("local:")
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


def resolve_credential(name: str, credentials: dict | None = None) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", name):
        return ""
    if os.getenv(name):
        return os.environ[name]
    if credentials is None:
        path = Path(os.getenv("SMARA_DESKTOP_CREDENTIALS", str(state_path().parent / "credentials.json")))
        credentials = read_object(path)
    value = next((credentials[key] for key in (name, name.upper(), name.lower()) if key in credentials), None)
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("protected"), str):
        try:
            return unprotect(value["protected"])
        except (OSError, ValueError, UnicodeError):
            return ""  # An unreadable alias must not prevent trying another.
    return ""


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
