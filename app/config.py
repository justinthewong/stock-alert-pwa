from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppSettings:
    base_url: str
    secret_key: str
    secure_cookies: bool


@dataclass(frozen=True)
class AuthSettings:
    username: str
    password_hash: str


@dataclass(frozen=True)
class VapidSettings:
    subject: str
    public_key: str
    private_key: str


@dataclass(frozen=True)
class DatabaseSettings:
    path: str


@dataclass(frozen=True)
class IbkrSettings:
    host: str
    port: int
    client_id: int
    trading_mode: str
    max_depth_symbols: int
    vnc_host: str
    vnc_port: int
    vnc_password: str


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    auth: AuthSettings
    vapid: VapidSettings
    database: DatabaseSettings
    ibkr: IbkrSettings


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(name: str, default: str = "") -> str:
    if os.getenv(name):
        return os.getenv(name, default)
    dotenv = _load_env_file(Path("/app/.env"))
    if name in dotenv:
        return dotenv[name]
    dotenv = _load_env_file(Path(".env"))
    return dotenv.get(name, default)


def get_vnc_password() -> str:
    return _env_value("VNC_SERVER_PASSWORD")


def get_vnc_host() -> str:
    return _env_value("IB_VNC_HOST", os.getenv("IB_GATEWAY_HOST", "127.0.0.1"))


def get_vnc_port() -> int:
    return int(_env_value("IB_VNC_PORT", "5900"))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Secrets file not found at {path}. Copy config/secrets.example.yaml to config/secrets.yaml."
        )
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Secrets file must contain a YAML mapping.")
    return data


@lru_cache
def get_settings() -> Settings:
    secrets_path = Path(os.getenv("SECRETS_PATH", "config/secrets.yaml"))
    data = _load_yaml(secrets_path)

    app_cfg = data.get("app", {})
    auth_cfg = data.get("auth", {})
    vapid_cfg = data.get("vapid", {})
    db_cfg = data.get("database", {})

    trading_mode = os.getenv("IB_TRADING_MODE", "paper").lower()
    default_port = 4003 if trading_mode == "live" else 4004

    return Settings(
        app=AppSettings(
            base_url=str(app_cfg.get("base_url", "http://localhost:8000")),
            secret_key=str(app_cfg.get("secret_key", "dev-secret-change-me")),
            secure_cookies=bool(app_cfg.get("secure_cookies", True)),
        ),
        auth=AuthSettings(
            username=str(auth_cfg.get("username", "admin")),
            password_hash=str(auth_cfg.get("password_hash", "")),
        ),
        vapid=VapidSettings(
            subject=str(vapid_cfg.get("subject", "mailto:admin@example.com")),
            public_key=str(vapid_cfg.get("public_key", "")),
            private_key=str(vapid_cfg.get("private_key", "")),
        ),
        database=DatabaseSettings(path=str(db_cfg.get("path", "data/alerts.db"))),
        ibkr=IbkrSettings(
            host=os.getenv("IB_GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("IB_GATEWAY_PORT", str(default_port))),
            client_id=int(os.getenv("IB_CLIENT_ID", "1")),
            trading_mode=trading_mode,
            max_depth_symbols=int(os.getenv("IB_MAX_DEPTH_SYMBOLS", "3")),
            vnc_host=get_vnc_host(),
            vnc_port=get_vnc_port(),
            vnc_password=get_vnc_password(),
        ),
    )
