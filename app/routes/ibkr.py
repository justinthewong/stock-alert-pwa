from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_auth
from app.schemas import IbkrStatusResponse
from app.services.ibkr_gateway import resolve_ibkr_status, trigger_gateway_login

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

    response_status = status_details.status if status_details.status != "disconnected" else "connecting"
    response_message = result.message if status_details.status == "connected" else status_details.message

    return IbkrStatusResponse(
        status=response_status,
        message=response_message,
        gateway_running=status_details.gateway_running,
        steps=result.steps,
        error=result.error,
        container_state=status_details.container_state,
        docker_available=status_details.docker_available,
        api_port_open=status_details.api_port_open,
    )
