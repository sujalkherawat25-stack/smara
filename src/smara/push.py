"""Web Push is optional and fails closed when VAPID is not configured."""
from __future__ import annotations
import json
from .config import settings


def enabled() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def send(store, account_id: str, title: str, body: str, url: str = "/app/") -> int:
    if not enabled():
        return 0
    from pywebpush import WebPushException, webpush
    delivered = 0
    for subscription in store.push_subscriptions(account_id):
        try:
            webpush(subscription_info={"endpoint": subscription["endpoint"], "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}}, data=json.dumps({"title": title[:120], "body": body[:300], "url": url}), vapid_private_key=settings.vapid_private_key, vapid_claims={"sub": settings.vapid_subject})
            delivered += 1
        except WebPushException as exc:
            if getattr(exc.response, "status_code", 0) in {404, 410}:
                store.delete_push_subscription(subscription["endpoint"])
    return delivered
