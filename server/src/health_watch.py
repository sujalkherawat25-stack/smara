"""Hourly error check for the Smara team: no model, no tokens, one Telegram message only when something went wrong.

Two things are counted while the server runs:
  - errors: every ERROR log record that carries an exception (an unhandled exception that a handler caught and logged,
    e.g. a failed agent run, a failed scheduler step, a failed webhook);
  - tool failures: a tool that raised inside an agent run (not a tool that answered with an error for a bad argument,
    which the model corrects by itself).
Once an hour the scheduler calls report_due(). If anything was counted it sends one short summary to the ops Telegram
chat (OPS_TELEGRAM_CHAT_ID) and starts counting again; if nothing was counted, it sends nothing. During quiet hours
nothing is sent: the counts keep growing and the first report after the quiet hours covers the whole night.
Counts live in this process only (lost on a restart, which is itself visible on the server).
"""
import logging
import time
from collections import Counter
from datetime import datetime

from src import clock

log = logging.getLogger(__name__)
EVERY_S = 3600
MAX_LINES = 8
_errors: Counter = Counter()
_tools: Counter = Counter()
_examples: dict[tuple, str] = {}
_since = time.time()
_last_report = time.monotonic()


class _ErrorCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if not record.exc_info or record.name == __name__:
            return
        try:
            exc = record.exc_info[0].__name__ if record.exc_info[0] else "Error"
            key = (record.name, str(record.msg)[:80], exc)
            _errors[key] += 1
            if key not in _examples:
                detail = str(record.exc_info[1])[:120] if record.exc_info[1] else ""
                _examples[key] = f"{exc}: {detail}" if detail else exc
        except Exception:
            pass                                       # counting must never break logging


_handler = _ErrorCounter()


def install() -> None:
    """Count errors from every logger. Safe to call more than once."""
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)


def tool_failed(name: str, err: BaseException) -> None:
    """A tool raised inside an agent run (called by the agent loop)."""
    key = (name, type(err).__name__)
    _tools[key] += 1
    _examples.setdefault(("tool",) + key, str(err)[:120])


def summary(now_wall: float | None = None) -> str:
    """The report text, or '' when nothing went wrong."""
    n_err, n_tool = sum(_errors.values()), sum(_tools.values())
    if not n_err and not n_tool:
        return ""
    start = datetime.fromtimestamp(_since, clock.IST).strftime("%d %b %H:%M")
    end = datetime.fromtimestamp(now_wall or time.time(), clock.IST).strftime("%H:%M")
    lines = [f"Smara health {start}–{end} IST: {n_err} error(s), {n_tool} tool failure(s)."]
    for (name, msg, exc), n in _errors.most_common(MAX_LINES):
        lines.append(f"• {n}× {name}: {msg.replace('%s', '…')} ({_examples.get((name, msg, exc), exc)})")
    for (name, exc), n in _tools.most_common(MAX_LINES):
        lines.append(f"• {n}× tool {name} raised {exc}: {_examples.get(('tool', name, exc), '')}")
    lines.append("Logs: docker logs smara_app --since 2h | grep -B2 -A20 Traceback")
    return "\n".join(lines)[:3500]


def reset() -> None:
    global _since
    _errors.clear()
    _tools.clear()
    _examples.clear()
    _since = time.time()


async def report_due(app, now: datetime) -> bool:
    """Called every scheduler tick. At most once an hour, outside quiet hours, and only when something was counted.
    Returns True when a report was sent."""
    global _last_report
    if time.monotonic() - _last_report < EVERY_S or clock.is_quiet(app.state.settings, now):
        return False
    _last_report = time.monotonic()
    text = summary()
    if not text:
        reset()
        return False
    chat, bot = getattr(app.state.settings, "ops_telegram_chat_id", None), getattr(app.state, "tg", None)
    if chat is None or bot is None:
        log.warning("health report not sent (no ops chat or Telegram bot): %s", text.splitlines()[0])
        reset()
        return False
    try:
        await bot.send({"chat_id": chat, "text": text})
    except Exception:
        log.warning("health report could not be sent; keeping the counts for the next hour")
        return False
    reset()
    return True
