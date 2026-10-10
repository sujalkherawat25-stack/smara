"""Give each pytest invocation its own Windows-safe temporary root."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def pytest_configure(config) -> None:
    if config.option.basetemp is None:
        build_root = Path(__file__).resolve().parent / "build"
        build_root.mkdir(exist_ok=True)
        config.option.basetemp = str(
            build_root / f"pytest-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
