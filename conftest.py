"""Give each pytest invocation its own Windows-safe temporary root."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def pytest_configure(config) -> None:
    if config.option.basetemp is None:
        config.option.basetemp = str(
            Path(__file__).resolve().parent
            / "build"
            / f"pytest-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
