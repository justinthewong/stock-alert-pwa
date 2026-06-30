from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from app.config import get_settings, get_vnc_password
from app.services.docker_socket import DockerSocketClient, DockerSocketError, get_docker_client
from app.worker import is_ibkr_connected

logger = logging.getLogger(__name__)

GatewayContainerState = Literal["running", "exited", "missing", "unavailable"]
IbkrStatus = Literal["disconnected", "connecting", "connected", "error"]


@dataclass
class IbkrLoginResult:
    ok: bool
    message: str
    steps: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class IbkrStatusDetails:
    status: IbkrStatus
    message: str
    gateway_running: bool
    steps: list[str] = field(default_factory=list)
    error: str | None = None
    container_state: GatewayContainerState = "unavailable"
    docker_available: bool = False
    api_port_open: bool = False
    vnc_available: bool = False


def _container_name() -> str:
    return os.getenv("IB_GATEWAY_CONTAINER", "stock-alert-ib-gateway")


def _vnc_configured() -> bool:
    return bool(get_vnc_password())


def _container_has_vnc(client: DockerSocketClient) -> bool:
    return bool(client.container_env_value(_container_name(), "VNC_SERVER_PASSWORD"))


def _needs_gateway_recreate(client: DockerSocketClient) -> bool:
    return _vnc_configured() and not _container_has_vnc(client)


def _connecting_status_message(vnc_available: bool, gateway_container_vnc: bool) -> tuple[str, str | None]:
    if vnc_available:
        return "Gateway is running. Complete login in the popup window.", None
    if _vnc_configured() and not gateway_container_vnc:
        return (
            "Gateway is running but needs to be recreated for the GUI popup.",
            "Click Connect IBKR to recreate the gateway with VNC enabled.",
        )
    return (
        "Gateway is running, waiting for login.",
        "Set VNC_SERVER_PASSWORD in .env and click Connect IBKR to use the GUI popup.",
    )


def _append_step(steps: list[str], message: str) -> None:
    steps.append(message)
    logger.info("IBKR login: %s", message)


def _docker_socket_available() -> bool:
    return os.path.exists("/var/run/docker.sock")


def _docker_api_available() -> tuple[bool, str]:
    client = get_docker_client()
    if client is None:
        return False, "Docker socket is not mounted into the app container."
    with client:
        return client.available()


def get_gateway_container_state(client: DockerSocketClient | None = None) -> GatewayContainerState:
    if client is None:
        client = get_docker_client()
    if client is None:
        return "unavailable"
    return client.container_state(_container_name())


async def is_api_port_open() -> bool:
    settings = get_settings().ibkr
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.host, settings.port),
            timeout=2,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _validate_gateway_prerequisites(steps: list[str]) -> str | None:
    env_path = "/app/.env"
    if os.path.isdir(env_path):
        return (
            "/app/.env is a directory, not a file. On the host, remove the .env directory "
            "and create a .env file from .env.example."
        )
    if not os.path.isfile(env_path):
        _append_step(steps, "Warning: /app/.env not found. IBKR credentials may be missing for the gateway.")
    return None


def _login_pending_message() -> str:
    if _vnc_configured():
        return "IB Gateway started. Complete login in the popup window."
    return "IB Gateway started. Set VNC_SERVER_PASSWORD in .env, then click Connect IBKR again."


def _recreate_gateway_container(client: DockerSocketClient, steps: list[str]) -> IbkrLoginResult:
    prerequisite_error = _validate_gateway_prerequisites(steps)
    if prerequisite_error:
        _append_step(steps, f"Error: {prerequisite_error}")
        return IbkrLoginResult(ok=False, message=prerequisite_error, steps=steps, error=prerequisite_error)

    try:
        for line in client.recreate_gateway_container(_container_name()):
            _append_step(steps, line)
    except DockerSocketError as exc:
        message = str(exc)
        details = exc.details or message
        _append_step(steps, f"Error: {message}")
        if exc.details:
            _append_step(steps, exc.details)
        return IbkrLoginResult(ok=False, message=message, steps=steps, error=details)

    return IbkrLoginResult(
        ok=True,
        message=_login_pending_message(),
        steps=steps,
    )


def _create_gateway_container(client: DockerSocketClient, steps: list[str]) -> IbkrLoginResult:
    prerequisite_error = _validate_gateway_prerequisites(steps)
    if prerequisite_error:
        _append_step(steps, f"Error: {prerequisite_error}")
        return IbkrLoginResult(ok=False, message=prerequisite_error, steps=steps, error=prerequisite_error)

    _append_step(steps, "Creating IB Gateway container via Docker API...")
    try:
        for line in client.create_gateway_container(_container_name()):
            _append_step(steps, line)
    except DockerSocketError as exc:
        message = str(exc)
        details = exc.details or message
        _append_step(steps, f"Error: {message}")
        if exc.details:
            _append_step(steps, exc.details)
        return IbkrLoginResult(ok=False, message=message, steps=steps, error=details)

    return IbkrLoginResult(
        ok=True,
        message=_login_pending_message(),
        steps=steps,
    )


def trigger_gateway_login() -> IbkrLoginResult:
    steps: list[str] = []
    _append_step(steps, "Connect IBKR requested.")

    client = get_docker_client()
    if client is None:
        message = "Docker socket is not available inside the app container."
        _append_step(steps, f"Error: {message}")
        return IbkrLoginResult(
            ok=False,
            message=message,
            steps=steps,
            error="Start ib-gateway manually on the host: docker compose --profile ibkr up -d ib-gateway",
        )

    with client:
        ok, docker_info = client.available()
        if not ok:
            message = "Could not access the Docker API from the app container."
            _append_step(steps, f"Error: {message}")
            _append_step(steps, docker_info)
            return IbkrLoginResult(ok=False, message=message, steps=steps, error=docker_info)

        _append_step(steps, docker_info or "Docker API is available.")

        state = client.container_state(_container_name())
        _append_step(steps, f"Gateway container state: {state}.")

        if state == "missing":
            return _create_gateway_container(client, steps)

        if state == "exited":
            if _needs_gateway_recreate(client):
                _append_step(steps, "Gateway container lacks VNC. Recreating with current settings...")
                return _recreate_gateway_container(client, steps)

            _append_step(steps, f"Starting container {_container_name()}...")
            try:
                _append_step(steps, client.start_container(_container_name()))
            except DockerSocketError as exc:
                message = str(exc)
                _append_step(steps, f"Error: {message}")
                if exc.details:
                    _append_step(steps, exc.details)
                return IbkrLoginResult(ok=False, message=message, steps=steps, error=exc.details or message)
            _append_step(steps, _login_pending_message())
            return IbkrLoginResult(
                ok=True,
                message=_login_pending_message(),
                steps=steps,
            )

        if state == "running":
            if is_ibkr_connected():
                message = "Already connected to IBKR."
                _append_step(steps, message)
                return IbkrLoginResult(ok=True, message=message, steps=steps)

            if _needs_gateway_recreate(client):
                _append_step(steps, "Gateway container lacks VNC. Recreating with current settings...")
                return _recreate_gateway_container(client, steps)

            _append_step(steps, "Gateway is running but API is not connected. Restarting container for a fresh login...")
            try:
                _append_step(steps, client.restart_container(_container_name()))
            except DockerSocketError as exc:
                message = str(exc)
                _append_step(steps, f"Error: {message}")
                if exc.details:
                    _append_step(steps, exc.details)
                return IbkrLoginResult(ok=False, message=message, steps=steps, error=exc.details or message)
            message = (
                "IB Gateway restarted. Complete login in the popup window."
                if _vnc_configured()
                else "IB Gateway restarted. Set VNC_SERVER_PASSWORD in .env, then click Connect IBKR again."
            )
            _append_step(steps, message)
            return IbkrLoginResult(
                ok=True,
                message=message,
                steps=steps,
            )

    message = "Could not determine gateway container state."
    _append_step(steps, f"Error: {message}")
    return IbkrLoginResult(ok=False, message=message, steps=steps, error=message)


def trigger_gateway_stop() -> IbkrLoginResult:
    steps: list[str] = []
    _append_step(steps, "Stop gateway requested.")

    client = get_docker_client()
    if client is None:
        message = "Docker socket is not available inside the app container."
        _append_step(steps, f"Error: {message}")
        return IbkrLoginResult(
            ok=False,
            message=message,
            steps=steps,
            error="Stop ib-gateway manually on the host: docker stop stock-alert-ib-gateway",
        )

    with client:
        ok, docker_info = client.available()
        if not ok:
            message = "Could not access the Docker API from the app container."
            _append_step(steps, f"Error: {message}")
            _append_step(steps, docker_info)
            return IbkrLoginResult(ok=False, message=message, steps=steps, error=docker_info)

        _append_step(steps, docker_info or "Docker API is available.")

        state = client.container_state(_container_name())
        _append_step(steps, f"Gateway container state: {state}.")

        if state in ("missing", "exited"):
            message = "IB Gateway is not running."
            _append_step(steps, message)
            return IbkrLoginResult(ok=True, message=message, steps=steps)

        if state == "running":
            try:
                _append_step(steps, client.stop_container(_container_name()))
            except DockerSocketError as exc:
                message = str(exc)
                _append_step(steps, f"Error: {message}")
                if exc.details:
                    _append_step(steps, exc.details)
                return IbkrLoginResult(ok=False, message=message, steps=steps, error=exc.details or message)
            message = "IB Gateway stopped."
            _append_step(steps, message)
            return IbkrLoginResult(ok=True, message=message, steps=steps)

    message = "Could not determine gateway container state."
    _append_step(steps, f"Error: {message}")
    return IbkrLoginResult(ok=False, message=message, steps=steps, error=message)


async def resolve_ibkr_status() -> IbkrStatusDetails:
    try:
        return await _resolve_ibkr_status()
    except Exception as exc:
        logger.exception("Failed to resolve IBKR status")
        return IbkrStatusDetails(
            status="error",
            message="Could not check IBKR status.",
            gateway_running=False,
            error=str(exc),
            container_state="unavailable",
            docker_available=_docker_socket_available(),
            api_port_open=False,
        )


async def _resolve_ibkr_status() -> IbkrStatusDetails:
    docker_available = False
    docker_error = ""
    container_state: GatewayContainerState = "unavailable"
    gateway_container_vnc = False

    client = get_docker_client()
    if client is not None:
        with client:
            docker_available, docker_error = client.available()
            if docker_available:
                container_state = client.container_state(_container_name())
                if container_state == "running" and _vnc_configured():
                    gateway_container_vnc = _container_has_vnc(client)

    gateway_running = container_state == "running"
    api_port_open = await is_api_port_open() if gateway_running else False
    vnc_available = _vnc_configured() and gateway_running and gateway_container_vnc

    if is_ibkr_connected():
        return IbkrStatusDetails(
            status="connected",
            message="Connected to IBKR.",
            gateway_running=gateway_running,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=True,
            vnc_available=vnc_available,
        )

    if not docker_available:
        if _docker_socket_available():
            return IbkrStatusDetails(
                status="error",
                message="Could not access the Docker API from the app container.",
                gateway_running=False,
                error=docker_error or "Check Docker socket permissions for the app container.",
                container_state=container_state,
                docker_available=False,
                api_port_open=False,
                vnc_available=False,
            )
        return IbkrStatusDetails(
            status="error",
            message="Docker socket is not available. IBKR login cannot be triggered from the dashboard.",
            gateway_running=False,
            error="Mount /var/run/docker.sock into the app container, or start ib-gateway manually.",
            container_state=container_state,
            docker_available=False,
            api_port_open=False,
            vnc_available=False,
        )

    if container_state == "exited":
        return IbkrStatusDetails(
            status="error",
            message="IB Gateway container has stopped.",
            gateway_running=False,
            error="Click Connect IBKR to start it again.",
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=False,
            vnc_available=False,
        )

    if container_state in ("missing", "unavailable"):
        return IbkrStatusDetails(
            status="disconnected",
            message="Not connected to IBKR. Click Connect IBKR to start the gateway.",
            gateway_running=False,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=False,
            vnc_available=False,
        )

    connecting_message, connecting_error = _connecting_status_message(vnc_available, gateway_container_vnc)

    if gateway_running and not api_port_open:
        if vnc_available:
            return IbkrStatusDetails(
                status="connecting",
                message=connecting_message,
                gateway_running=True,
                container_state=container_state,
                docker_available=docker_available,
                api_port_open=False,
                vnc_available=True,
            )
        return IbkrStatusDetails(
            status="disconnected",
            message=connecting_message,
            gateway_running=True,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=False,
            vnc_available=False,
            error=connecting_error,
        )

    if gateway_running:
        return IbkrStatusDetails(
            status="connecting",
            message="Gateway API port is open. Waiting for the app worker to connect...",
            gateway_running=True,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=True,
            vnc_available=vnc_available,
        )

    return IbkrStatusDetails(
        status="disconnected",
        message="Not connected to IBKR.",
        gateway_running=False,
        container_state=container_state,
        docker_available=docker_available,
        api_port_open=False,
        vnc_available=False,
    )
