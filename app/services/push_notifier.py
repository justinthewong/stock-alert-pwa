from __future__ import annotations

import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Alert, get_engine
from app.services.alert_service import list_push_subscriptions, log_event

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


def _send_to_all(payload: dict[str, Any], log_alert_id: int | None, log_event_name: str) -> int:
    settings = get_settings()
    if not settings.vapid.public_key or not settings.vapid.private_key:
        logger.warning("VAPID keys are not configured; skipping push notifications.")
        return 0

    data = json.dumps(payload)
    sent = 0
    session = _session()
    try:
        for subscription in list_push_subscriptions(session):
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=data,
                    vapid_private_key=settings.vapid.private_key,
                    vapid_claims={"sub": settings.vapid.subject},
                )
                sent += 1
            except WebPushException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                logger.warning("Push failed for subscription %s: %s", subscription.id, exc)
                if status_code in {404, 410}:
                    session.delete(subscription)
        log_event(session, log_alert_id, log_event_name, f"sent={sent}")
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return sent


def send_alert_notification(alert: Alert, available: int) -> int:
    return _send_to_all(_build_payload(alert, available), alert.id, "push_sent")


def send_test_notification() -> int:
    return _send_to_all(
        {
            "title": "Test notification",
            "body": "Push notifications are working on this device.",
            "url": "/dashboard",
        },
        None,
        "test_push_sent",
    )
