from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status

from app.auth import SESSION_COOKIE, read_session_token, require_auth
from app.config import get_vnc_password
from app.schemas import IbkrStatusResponse
from app.services.ibkr_gateway import get_gateway_container_state, resolve_ibkr_status, trigger_gateway_login
from app.services.vnc_proxy import relay_vnc_websocket

router = APIRouter(prefix="/api/ibkr", tags=["ibkr"])


def _to_response(details) -> IbkrStatusResponse:
    return IbkrStatusResponse(
        status=details.status,
        message=details.message,
        gateway_running=details.gateway_running,
        steps=details.steps,
        error=details.error,
        container_state=details.container_state,
        docker_available=details.docker_available,
        api_port_open=details.api_port_open,
        vnc_available=details.vnc_available,
        vnc_configured=bool(get_vnc_password()),
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

    response_status = status_details.status
    if response_status == "disconnected" and status_details.gateway_running:
        response_status = "connecting"
    response_message = result.message if status_details.status == "connected" else status_details.message

    return IbkrStatusResponse(
        status=response_status,
        message=response_message,
        gateway_running=status_details.gateway_running,
        steps=result.steps,
        error=status_details.error or result.error,
        container_state=status_details.container_state,
        docker_available=status_details.docker_available,
        api_port_open=status_details.api_port_open,
        vnc_available=status_details.vnc_available,
        vnc_configured=bool(get_vnc_password()),
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
