from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Literal

from app.config import get_settings
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


def _compose_file() -> str:
    return os.getenv("COMPOSE_FILE", "/app/docker-compose.yml")


def _docker_socket_available() -> bool:
    return os.path.exists("/var/run/docker.sock")


def _docker_cli_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def get_gateway_container_state() -> GatewayContainerState:
    if not _docker_socket_available():
        return "unavailable"

    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", _container_name()],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        logger.warning("docker CLI not found while checking gateway container state")
        return "unavailable"
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("docker inspect failed: %s", exc)
        return "unavailable"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "No such object" in stderr or "Error: No such object" in stderr:
            return "missing"
        logger.warning("docker inspect failed: %s", stderr)
        return "unavailable"

    running = result.stdout.strip().lower()
    if running == "true":
        return "running"
    return "exited"


def _append_step(steps: list[str], message: str) -> None:
    steps.append(message)
    logger.info("IBKR login: %s", message)


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


def _run_docker_command(args: list[str], *, cwd: str | None = None) -> tuple[bool, str, str]:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, "", str(exc)

    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    if result.returncode != 0:
        return False, output, output or "docker command failed"
    return True, output, ""


def _validate_compose_prerequisites(steps: list[str]) -> str | None:
    compose_file = _compose_file()
    if not os.path.exists(compose_file):
        return f"Compose file not found at {compose_file}."

    env_path = "/app/.env"
    if os.path.isdir(env_path):
        return (
            "/app/.env is a directory, not a file. On the host, remove the .env directory "
            "and create a .env file from .env.example."
        )
    if not os.path.exists(env_path):
        _append_step(steps, "Warning: /app/.env not found. Gateway credentials must be available to Docker Compose.")

    return None


def _compose_command(*extra_args: str) -> list[str]:
    command = ["compose", "-f", _compose_file()]
    project_name = os.getenv("COMPOSE_PROJECT_NAME")
    if project_name:
        command.extend(["-p", project_name])
    command.extend(["--profile", "ibkr", *extra_args])
    return command


def _create_gateway_container(steps: list[str]) -> IbkrLoginResult:
    prerequisite_error = _validate_compose_prerequisites(steps)
    if prerequisite_error:
        _append_step(steps, f"Error: {prerequisite_error}")
        return IbkrLoginResult(ok=False, message=prerequisite_error, steps=steps, error=prerequisite_error)

    _append_step(steps, "Creating IB Gateway container with Docker Compose...")
    ok, output, err = _run_docker_command(
        _compose_command("up", "-d", "ib-gateway"),
        cwd="/app",
    )
    if output:
        for line in output.splitlines():
            _append_step(steps, line)
    if not ok:
        message = f"Could not create gateway container: {err}"
        _append_step(steps, f"Error: {message}")
        return IbkrLoginResult(ok=False, message=message, steps=steps, error=err)

    _append_step(steps, "IB Gateway container started. Waiting for login and 2FA approval...")
    return IbkrLoginResult(
        ok=True,
        message="IB Gateway started. Approve 2FA on your phone if prompted.",
        steps=steps,
    )


def trigger_gateway_login() -> IbkrLoginResult:
    steps: list[str] = []
    _append_step(steps, "Connect IBKR requested.")

    if not _docker_socket_available():
        message = "Docker socket is not available inside the app container."
        _append_step(steps, f"Error: {message}")
        return IbkrLoginResult(
            ok=False,
            message=message,
            steps=steps,
            error="Start ib-gateway manually on the host: docker compose --profile ibkr up -d ib-gateway",
        )

    if not _docker_cli_available():
        message = "Docker CLI is not available inside the app container."
        _append_step(steps, f"Error: {message}")
        return IbkrLoginResult(
            ok=False,
            message=message,
            steps=steps,
            error="Rebuild the app image: docker compose up -d --build app",
        )

    _append_step(steps, "Docker socket is available.")

    state = get_gateway_container_state()
    _append_step(steps, f"Gateway container state: {state}.")

    if state == "missing":
        return _create_gateway_container(steps)

    if state == "exited":
        _append_step(steps, f"Starting container {_container_name()}...")
        ok, output, err = _run_docker_command(["start", _container_name()])
        if output:
            _append_step(steps, output)
        if not ok:
            message = f"Could not start gateway container: {err}"
            _append_step(steps, f"Error: {message}")
            return IbkrLoginResult(ok=False, message=message, steps=steps, error=err)
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
        ok, output, err = _run_docker_command(["restart", _container_name()])
        if output:
            _append_step(steps, output)
        if not ok:
            message = f"Could not restart gateway container: {err}"
            _append_step(steps, f"Error: {message}")
            return IbkrLoginResult(ok=False, message=message, steps=steps, error=err)
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
    docker_available = _docker_socket_available() and _docker_cli_available()
    container_state = get_gateway_container_state()
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
        if _docker_socket_available() and not _docker_cli_available():
            return IbkrStatusDetails(
                status="error",
                message="Docker CLI is not available inside the app container.",
                gateway_running=False,
                error="Rebuild the app image so it includes the docker CLI: docker compose up -d --build app",
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
