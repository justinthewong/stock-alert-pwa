from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_username

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
