from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.auth import clear_session_cookie, require_auth, set_session_cookie, verify_password
from app.config import get_settings
from app.schemas import LoginRequest

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login")
def login(payload: LoginRequest):
    settings = get_settings()
    if payload.username != settings.auth.username or not verify_password(
        payload.password, settings.auth.password_hash
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    response = JSONResponse({"ok": True})
    set_session_cookie(response, payload.username)
    return response


@router.post("/logout")
def logout(_: str = Depends(require_auth)):
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response
