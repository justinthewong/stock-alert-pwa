from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_session
from app.schemas import AlertCreate, AlertResponse
from app.services.alert_service import create_alert, delete_alert, list_alerts

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertResponse])
def get_alerts(_: str = Depends(require_auth), session: Session = Depends(get_session)):
    return list_alerts(session)


@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def post_alert(
    payload: AlertCreate,
    _: str = Depends(require_auth),
    session: Session = Depends(get_session),
):
    alert = create_alert(session, payload)
    return alert


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_alert(
    alert_id: int,
    _: str = Depends(require_auth),
    session: Session = Depends(get_session),
):
    if not delete_alert(session, alert_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
