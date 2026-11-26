"""The app's own warnings and errors, printed to stderr (so `docker logs smara_app` shows them), with secrets hidden.

With no handler on the root logger Python prints warnings by itself. Adding any handler there (health_watch's error
counter) switches that off, so the printing has to be set up by hand."""
import logging
import os
import re
import sys

HIDDEN = "<hidden>"
_SECRET_NAMES = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL|_DSN", re.I)
_PATTERNS = (
    (re.compile(r"(api\.telegram\.org/(?:file/)?bot)[^/\s\"'?]+"), r"\1" + HIDDEN),      # the bot token is in the address
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@\"']*:[^/\s\"']+@", re.I), r"\1" + HIDDEN + "@"),     # user:password@host
    (re.compile(r"([?&](?:[a-z_.]*token|[a-z_]*key|[a-z_]*secret|signature|sig|code|auth)=)[^&\s\"']+", re.I), r"\1" + HIDDEN),
    (re.compile(r"\b(password|passwd|pwd)=[^\s\"'&]+", re.I), r"\1=" + HIDDEN),
    (re.compile(r"\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1" + HIDDEN),
    (re.compile(r"(?<![\w-])\+?\d{6,}(\d{4})(?![\w-])"), r"...\1"),                     # phone and account numbers: last 4
)


def _secret_values() -> list[str]:
    found = set()
    for name, value in os.environ.items():
        if _SECRET_NAMES.search(name):
            found.update(part.strip() for part in value.split(",") if len(part.strip()) >= 8)     # LLM_API_KEYS is a list
    return sorted(found, key=len, reverse=True)


def redact(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, HIDDEN)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _Redacting(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class _Stderr(logging.StreamHandler):
    def __init__(self):
        logging.Handler.__init__(self, level=logging.WARNING)

    @property
    def stream(self):                       # whatever stderr is now, not what it was at start-up
        return sys.stderr


_handler = _Stderr()
_handler.setFormatter(_Redacting("%(asctime)s %(levelname)s %(name)s: %(message)s"))


def install() -> None:
    """Safe to call more than once."""
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)
