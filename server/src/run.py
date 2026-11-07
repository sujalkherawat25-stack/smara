"""Server entrypoint: `python -m src.run`. Needed on Windows, where uvicorn's
default Proactor loop is incompatible with psycopg async."""
import asyncio
import selectors

import os
from pathlib import Path

import uvicorn


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    config = uvicorn.Config("src.main:app", host="0.0.0.0", port=8080)
    asyncio.run(
        uvicorn.Server(config).serve(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )


if __name__ == "__main__":
    main()
