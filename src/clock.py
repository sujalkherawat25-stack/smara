"""Business time. The server runs on UTC; Indian shops live on IST (UTC+5:30), so
"today", "overdue" and "due this week" must be computed in IST, not server time."""
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def today() -> date:
    return datetime.now(IST).date()
