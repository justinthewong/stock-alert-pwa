from __future__ import annotations

import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush

from app.config import get_settings
from app.database import Alert, PushSubscription, get_engine
from app.services.alert_service import list_push_subscriptions, log_event
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _session() -> Session:
    SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return SessionLocal()


def _build_payload(alert: Alert, available: int) -> dict[str, Any]:
    side_label = "Buy" if alert.side == "buy" else "Sell"
    return {
        "title": f"{alert.ticker} alert triggered",
        "body": (
            f"{side_label} {alert.share_count:,} shares at or better than "
            f"${alert.target_price:.2f}. Available: {available:,}."
        ),
        "url": "/dashboard",
        "alert_id": alert.id,
        "ticker": alert.ticker,
        "available": available,
    }


def send_alert_notification(alert: Alert, available: int) -> int:
    settings = get_settings()
    if not settings.vapid.public_key or not settings.vapid.private_key:
        logger.warning("VAPID keys are not configured; skipping push notifications.")
        return 0

    payload = json.dumps(_build_payload(alert, available))
    sent = 0
    session = _session()
    try:
        subscriptions = list_push_subscriptions(session)
        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=payload,
                    vapid_private_key=settings.vapid.private_key,
                    vapid_claims={"sub": settings.vapid.subject},
                )
                sent += 1
            except WebPushException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                logger.warning("Push failed for subscription %s: %s", subscription.id, exc)
                if status_code in {404, 410}:
                    session.delete(subscription)
        log_event(session, alert.id, "push_sent", f"sent={sent}")
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return sent
