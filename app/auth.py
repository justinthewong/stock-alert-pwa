from __future__ import annotations

import bcrypt
from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings

SESSION_COOKIE = "stock_alert_session"


def _serializer() -> URLSafeSerializer:
    settings = get_settings()
    return URLSafeSerializer(settings.app.secret_key, salt="stock-alert-session")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_session_token(username: str) -> str:
    return _serializer().dumps({"username": username})


def read_session_token(token: str) -> str | None:
    try:
        payload = _serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    username = payload.get("username")
    return str(username) if username else None


def set_session_cookie(response, username: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(username),
        httponly=True,
        secure=settings.app.secure_cookies,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def get_current_username(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return read_session_token(token)


def require_auth(request: Request) -> str:
    username = get_current_username(request)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return username
