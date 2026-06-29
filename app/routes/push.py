from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.config import get_settings
from app.database import get_session
from app.schemas import PublicConfigResponse, PushSubscriptionRequest
from app.services.alert_service import save_push_subscription

router = APIRouter(prefix="/api", tags=["push"])


@router.get("/config", response_model=PublicConfigResponse)
def public_config():
    settings = get_settings()
    return PublicConfigResponse(vapid_public_key=settings.vapid.public_key)


@router.post("/push/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe_push(
    payload: PushSubscriptionRequest,
    request: Request,
    _: str = Depends(require_auth),
    session: Session = Depends(get_session),
):
    save_push_subscription(
        session,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth_key=payload.keys.auth,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}
