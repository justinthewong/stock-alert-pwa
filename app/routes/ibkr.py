from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_auth
from app.schemas import IbkrStatusResponse
from app.services.ibkr_gateway import resolve_ibkr_status, trigger_gateway_login

router = APIRouter(prefix="/api/ibkr", tags=["ibkr"])


@router.get("/status", response_model=IbkrStatusResponse)
async def ibkr_status(_: str = Depends(require_auth)):
    ibkr_status_value, message, gateway_running = await resolve_ibkr_status()
    return IbkrStatusResponse(
        status=ibkr_status_value,
        message=message,
        gateway_running=gateway_running,
    )


@router.post("/login", response_model=IbkrStatusResponse)
async def ibkr_login(_: str = Depends(require_auth)):
    ok, message = trigger_gateway_login()
    if not ok:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)

    ibkr_status_value, status_message, gateway_running = await resolve_ibkr_status()
    return IbkrStatusResponse(
        status=ibkr_status_value if ibkr_status_value != "disconnected" else "connecting",
        message=message if ibkr_status_value == "connected" else status_message or message,
        gateway_running=gateway_running,
    )
