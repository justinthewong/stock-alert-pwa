from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_username
from app.config import get_vnc_password
from app.services.ibkr_gateway import get_gateway_container_state

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_username(request):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": None},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    username = get_current_username(request)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"request": request, "username": username},
    )


@router.get("/ibkr/vnc", response_class=HTMLResponse)
def ibkr_vnc_page(request: Request):
    username = get_current_username(request)
    if not username:
        return RedirectResponse(url="/", status_code=303)

    if not get_vnc_password():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set VNC_SERVER_PASSWORD in .env to enable the IB Gateway GUI popup.",
        )

    if get_gateway_container_state() != "running":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IB Gateway is not running. Click Connect IBKR first.",
        )

    return templates.TemplateResponse(
        request,
        "ibkr_vnc.html",
        {
            "request": request,
            "vnc_password": get_vnc_password(),
        },
    )
