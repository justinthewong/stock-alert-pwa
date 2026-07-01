from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status

from app.auth import SESSION_COOKIE, read_session_token, require_auth
from app.config import get_vnc_password
from app.schemas import IbkrStatusResponse
from app.services.ibkr_gateway import (
    get_gateway_container_state,
    resolve_ibkr_status,
    trigger_gateway_login,
    trigger_gateway_stop,
)
from app.services.vnc_proxy import relay_vnc_websocket

router = APIRouter(prefix="/api/ibkr", tags=["ibkr"])


def _vnc_login_required(details) -> bool:
    if details.status in ("connected", "error") or not details.gateway_running:
        return False
    return bool(details.vnc_available or get_vnc_password())


def _to_response(details, *, steps=None, message=None) -> IbkrStatusResponse:
    response_status = details.status
    if response_status == "disconnected" and details.gateway_running:
        response_status = "connecting"
    return IbkrStatusResponse(
        status=response_status,
        message=message if message is not None else details.message,
        gateway_running=details.gateway_running,
        steps=steps if steps is not None else details.steps,
        error=details.error,
        container_state=details.container_state,
        docker_available=details.docker_available,
        api_port_open=details.api_port_open,
        vnc_available=details.vnc_available,
        vnc_configured=bool(get_vnc_password()),
        vnc_login_required=_vnc_login_required(details),
        gateway_authenticated=details.gateway_authenticated,
        worker_connected=details.worker_connected,
        worker_state=details.worker_state,
        worker_last_error=details.worker_last_error,
        depth_subscriptions=details.depth_subscriptions,
        market_data_active=details.market_data_active,
    )


@router.get("/status", response_model=IbkrStatusResponse)
async def ibkr_status(_: str = Depends(require_auth)):
    return _to_response(await resolve_ibkr_status())


@router.post("/login", response_model=IbkrStatusResponse)
async def ibkr_login(_: str = Depends(require_auth)):
    result = trigger_gateway_login()
    status_details = await resolve_ibkr_status()

    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": result.message,
                "error": result.error,
                "steps": result.steps,
            },
        )

    response_message = result.message if status_details.status == "connected" else status_details.message
    return _to_response(
        status_details,
        steps=result.steps,
        message=response_message,
    )


@router.post("/stop", response_model=IbkrStatusResponse)
async def ibkr_stop(_: str = Depends(require_auth)):
    result = trigger_gateway_stop()
    status_details = await resolve_ibkr_status()

    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": result.message,
                "error": result.error,
                "steps": result.steps,
            },
        )

    response_error = status_details.error or result.error
    if not status_details.gateway_running:
        status_details.status = "disconnected"
        status_details.error = None
        response_error = None

    return _to_response(
        status_details,
        steps=result.steps,
        message=result.message,
    )


@router.websocket("/vnc/ws")
async def ibkr_vnc_websocket(websocket: WebSocket):
    username = read_session_token(websocket.cookies.get(SESSION_COOKIE, ""))
    if not username:
        await websocket.close(code=4401, reason="Authentication required.")
        return

    if not get_vnc_password():
        await websocket.close(code=4403, reason="VNC is not configured.")
        return

    if get_gateway_container_state() != "running":
        await websocket.close(code=4404, reason="IB Gateway is not running.")
        return

    await relay_vnc_websocket(websocket)
