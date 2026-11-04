"""Business time. The server runs on UTC; Indian shops live on IST (UTC+5:30), so
"today", "overdue", "due this week" and quiet hours are all computed in IST."""
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def now() -> datetime:
    return datetime.now(IST)


def today() -> date:
    return now().date()


def parse_quiet_hours(spec: str, default: tuple[int, int] = (21, 8)) -> tuple[int, int]:
    """'21-8' -> (21, 8): quiet from 21:00 until 08:00 IST."""
    try:
        a, b = (int(x) for x in spec.split("-"))
        return (a, b) if 0 <= a <= 23 and 0 <= b <= 23 else default
    except (ValueError, AttributeError):
        return default


def is_quiet(settings, at: datetime | None = None) -> bool:
    """True inside the shop's quiet hours (nothing proactive is sent to anyone)."""
    start, end = settings.quiet_start_hour, settings.quiet_end_hour
    h = (at or now()).astimezone(IST).hour
    return (h >= start or h < end) if start > end else (start <= h < end)


def next_open(settings, at: datetime | None = None) -> datetime:
    """The next moment proactive messages are allowed (now, if not quiet)."""
    at = (at or now()).astimezone(IST)
    if not is_quiet(settings, at):
        return at
    opening = at.replace(hour=settings.quiet_end_hour, minute=0, second=0, microsecond=0)
    return opening if opening > at else opening + timedelta(days=1)


def describe(at: datetime | None = None) -> str:
    return (at or now()).astimezone(IST).strftime("%A %d %B %Y, %H:%M IST")
