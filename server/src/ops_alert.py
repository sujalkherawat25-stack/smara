"""Tells the Smara team (on Telegram) when owner messages cannot be sent.

One message per problem per hour, so a broken channel does not flood the chat with one alert per message.
Never raises: an alert must not break the agent run that found the problem.
"""
import logging
import time

log = logging.getLogger(__name__)
ALERT_EVERY_S = 3600
_last_sent: dict[str, float] = {}


async def ops_alert(app, key: str, text: str) -> bool:
    """Send `text` to the ops chat unless the same `key` was sent within the hour. Returns True if it was sent."""
    settings = getattr(app.state, "settings", None)
    chat = getattr(settings, "ops_telegram_chat_id", None)
    bot = getattr(app.state, "tg", None)
    if chat is None or bot is None:
        log.warning("ops alert not sent (no ops chat or Telegram bot configured): %s", text)
        return False
    now = time.monotonic()
    if key in _last_sent and now - _last_sent[key] < ALERT_EVERY_S:
        return False
    try:
        await bot.send({"chat_id": chat, "text": text})
    except Exception:
        log.exception("ops alert could not be sent: %s", key)
        return False
    _last_sent[key] = now
    return True
