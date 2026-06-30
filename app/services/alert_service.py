from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import Alert, AlertLog, PushSubscription
from app.schemas import AlertCreate


def list_alerts(session: Session) -> list[Alert]:
    return list(session.scalars(select(Alert).order_by(Alert.created_at.desc())).all())


def list_active_alerts(session: Session) -> list[Alert]:
    return list(
        session.scalars(
            select(Alert).where(Alert.status == "active").order_by(Alert.created_at.asc())
        ).all()
    )


def get_alert(session: Session, alert_id: int) -> Alert | None:
    return session.get(Alert, alert_id)


def create_alert(session: Session, payload: AlertCreate) -> Alert:
    alert = Alert(
        ticker=payload.ticker,
        side=payload.side,
        share_count=payload.share_count,
        target_price=payload.target_price,
        status="active",
        created_at=datetime.utcnow(),
    )
    session.add(alert)
    session.flush()
    log_event(session, alert.id, "created", f"{payload.side} {payload.share_count} @ {payload.target_price}")
    return alert


def delete_alert(session: Session, alert_id: int) -> bool:
    alert = session.get(Alert, alert_id)
    if alert is None:
        return False
    session.execute(delete(AlertLog).where(AlertLog.alert_id == alert_id))
    session.delete(alert)
    return True


def mark_checked(session: Session, alert: Alert, depth_json: str | None = None) -> None:
    alert.last_checked_at = datetime.utcnow()
    if depth_json is not None:
        alert.last_depth_json = depth_json
    session.add(alert)


def mark_triggered(session: Session, alert: Alert, depth_json: str | None = None) -> None:
    alert.status = "triggered"
    alert.triggered_at = datetime.utcnow()
    alert.last_checked_at = datetime.utcnow()
    if depth_json is not None:
        alert.last_depth_json = depth_json
    session.add(alert)
    log_event(session, alert.id, "triggered", depth_json)


def log_event(session: Session, alert_id: int | None, event: str, detail: str | None = None) -> None:
    session.add(
        AlertLog(
            alert_id=alert_id,
            event=event,
            detail=detail,
            created_at=datetime.utcnow(),
        )
    )


def save_push_subscription(
    session: Session,
    endpoint: str,
    p256dh: str,
    auth_key: str,
    user_agent: str | None,
) -> PushSubscription:
    existing = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if existing:
        existing.p256dh = p256dh
        existing.auth = auth_key
        existing.user_agent = user_agent
        session.add(existing)
        return existing

    subscription = PushSubscription(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth_key,
        user_agent=user_agent,
        created_at=datetime.utcnow(),
    )
    session.add(subscription)
    session.flush()
    return subscription


def list_push_subscriptions(session: Session) -> list[PushSubscription]:
    return list(session.scalars(select(PushSubscription).order_by(PushSubscription.created_at.desc())).all())


def active_tickers(alerts: Iterable[Alert]) -> list[str]:
    seen: set[str] = set()
    tickers: list[str] = []
    for alert in alerts:
        if alert.ticker not in seen:
            seen.add(alert.ticker)
            tickers.append(alert.ticker)
    return tickers


def serialize_depth(levels) -> str:
    payload = [{"price": level.price, "size": level.size} for level in levels]
    return json.dumps(payload)
