"""Smara Desktop updates, served by Smara itself.

Each release puts the signed Windows installer and a small manifest (latest.json) in DESKTOP_RELEASES_DIR (a named volume,
so releases of the server keep it). The app asks GET /desktop/update/windows/x86_64/<its version>; it gets 204 when it is
current, else the version, notes, download address and the installer's signature. The app checks that signature against
the public key built into it before installing, so a changed file on this server cannot be installed."""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

router = APIRouter(prefix="/desktop")
FILE = re.compile(r"^[A-Za-z0-9 ._-]{1,120}\.exe$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
DEFAULT_BASE = "https://ai.syntarus.com/smara"


def releases_dir(app) -> Path:
    return Path(getattr(app.state.settings, "desktop_releases_dir", "") or "./data/desktop-releases")


def _version(text: str) -> tuple[int, ...] | None:
    return tuple(int(p) for p in text.split(".")) if VERSION.match(text or "") else None


def manifest(app) -> dict | None:
    try:
        data = json.loads((releases_dir(app) / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (_version(str(data.get("version"))) and FILE.match(str(data.get("file") or "")) and data.get("signature")):
        return None
    if not (releases_dir(app) / data["file"]).is_file():
        return None
    return data


@router.get("/update/{target}/{arch}/{current}")
async def update(target: str, arch: str, current: str, request: Request):
    app = request.app
    latest = manifest(app)
    have = _version(current)
    if latest is None or have is None or target != "windows" or arch != "x86_64" or _version(latest["version"]) <= have:
        return Response(status_code=204)
    base = (getattr(app.state.settings, "public_base_url", "") or DEFAULT_BASE).rstrip("/")
    return {"version": latest["version"], "notes": str(latest.get("notes") or "")[:2000],
            "pub_date": latest.get("pub_date") or None,
            "url": f"{base}/desktop/download/{latest['file'].replace(' ', '%20')}", "signature": latest["signature"]}


@router.get("/download/{name}")
async def download(name: str, request: Request):
    if not FILE.match(name):
        raise HTTPException(status_code=404)
    path = releases_dir(request.app) / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/octet-stream", filename=name)
