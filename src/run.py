"""Server entrypoint: `python -m src.run`. Needed on Windows, where uvicorn's
default Proactor loop is incompatible with psycopg async."""
import asyncio
import selectors

import uvicorn


def main() -> None:
    config = uvicorn.Config("src.main:app", host="0.0.0.0", port=8080)
    asyncio.run(
        uvicorn.Server(config).serve(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )


if __name__ == "__main__":
    main()
