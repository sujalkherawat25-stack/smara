"""Optional error reporting. Importing Smara never requires a Sentry account."""
from __future__ import annotations


def configure_sentry(dsn: str) -> None:
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError as exc:
        raise RuntimeError("SMARA_SENTRY_DSN is set but sentry-sdk is not installed.") from exc
    sentry_sdk.init(dsn=dsn, traces_sample_rate=0.05, send_default_pii=False)
