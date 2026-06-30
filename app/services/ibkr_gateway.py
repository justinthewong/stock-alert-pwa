from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from app.config import get_settings
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


def _container_name() -> str:
    return os.getenv("IB_GATEWAY_CONTAINER", "stock-alert-ib-gateway")


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
        message="IB Gateway started. Approve 2FA on your phone if prompted.",
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
        ok, docker_error = client.available()
        if not ok:
            message = "Could not access the Docker API from the app container."
            _append_step(steps, f"Error: {message}")
            _append_step(steps, docker_error)
            return IbkrLoginResult(ok=False, message=message, steps=steps, error=docker_error)

        _append_step(steps, "Docker API is available.")

        state = client.container_state(_container_name())
        _append_step(steps, f"Gateway container state: {state}.")

        if state == "missing":
            return _create_gateway_container(client, steps)

        if state == "exited":
            _append_step(steps, f"Starting container {_container_name()}...")
            try:
                _append_step(steps, client.start_container(_container_name()))
            except DockerSocketError as exc:
                message = str(exc)
                _append_step(steps, f"Error: {message}")
                if exc.details:
                    _append_step(steps, exc.details)
                return IbkrLoginResult(ok=False, message=message, steps=steps, error=exc.details or message)
            _append_step(steps, "Container started. Approve 2FA on your phone if prompted.")
            return IbkrLoginResult(
                ok=True,
                message="IB Gateway started. Approve 2FA on your phone if prompted.",
                steps=steps,
            )

        if state == "running":
            if is_ibkr_connected():
                message = "Already connected to IBKR."
                _append_step(steps, message)
                return IbkrLoginResult(ok=True, message=message, steps=steps)

            _append_step(steps, "Gateway is running but API is not connected. Restarting container for a fresh login...")
            try:
                _append_step(steps, client.restart_container(_container_name()))
            except DockerSocketError as exc:
                message = str(exc)
                _append_step(steps, f"Error: {message}")
                if exc.details:
                    _append_step(steps, exc.details)
                return IbkrLoginResult(ok=False, message=message, steps=steps, error=exc.details or message)
            _append_step(steps, "Container restarted. Approve 2FA on your phone if prompted.")
            return IbkrLoginResult(
                ok=True,
                message="IB Gateway restarted. Approve 2FA on your phone if prompted.",
                steps=steps,
            )

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

    client = get_docker_client()
    if client is not None:
        with client:
            docker_available, docker_error = client.available()
            if docker_available:
                container_state = client.container_state(_container_name())

    gateway_running = container_state == "running"
    api_port_open = await is_api_port_open() if gateway_running else False

    if is_ibkr_connected():
        return IbkrStatusDetails(
            status="connected",
            message="Connected to IBKR.",
            gateway_running=gateway_running,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=True,
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
            )
        return IbkrStatusDetails(
            status="error",
            message="Docker socket is not available. IBKR login cannot be triggered from the dashboard.",
            gateway_running=False,
            error="Mount /var/run/docker.sock into the app container, or start ib-gateway manually.",
            container_state=container_state,
            docker_available=False,
            api_port_open=False,
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
        )

    if container_state in ("missing", "unavailable"):
        return IbkrStatusDetails(
            status="disconnected",
            message="Not connected to IBKR. Click Connect IBKR to start the gateway.",
            gateway_running=False,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=False,
        )

    if gateway_running and not api_port_open:
        return IbkrStatusDetails(
            status="connecting",
            message="Gateway is running. Approve 2FA on your phone if prompted.",
            gateway_running=True,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=False,
        )

    if gateway_running:
        return IbkrStatusDetails(
            status="connecting",
            message="Gateway API port is open. Waiting for the app worker to connect...",
            gateway_running=True,
            container_state=container_state,
            docker_available=docker_available,
            api_port_open=True,
        )

    return IbkrStatusDetails(
        status="disconnected",
        message="Not connected to IBKR.",
        gateway_running=False,
        container_state=container_state,
        docker_available=docker_available,
        api_port_open=False,
    )
