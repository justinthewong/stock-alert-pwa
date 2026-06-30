from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Literal

from app.config import get_settings
from app.worker import is_ibkr_connected

logger = logging.getLogger(__name__)

GatewayContainerState = Literal["running", "exited", "missing", "unavailable"]
IbkrStatus = Literal["disconnected", "connecting", "connected", "error"]


def _container_name() -> str:
    return os.getenv("IB_GATEWAY_CONTAINER", "stock-alert-ib-gateway")


def _docker_socket_available() -> bool:
    return os.path.exists("/var/run/docker.sock")


def get_gateway_container_state() -> GatewayContainerState:
    if not _docker_socket_available():
        return "unavailable"

    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", _container_name()],
        capture_output=True,
        text=True,
        timeout=10,
    )
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


def _run_docker_command(args: list[str], *, cwd: str | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)

    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or "docker command failed"
    return True, ""


def _create_gateway_container() -> tuple[bool, str]:
    compose_file = "/app/docker-compose.yml"
    if not os.path.exists(compose_file):
        return False, (
            "IB Gateway container does not exist. "
            "Create it with: docker compose --profile ibkr up -d ib-gateway"
        )

    ok, err = _run_docker_command(
        ["compose", "-f", compose_file, "--profile", "ibkr", "up", "-d", "ib-gateway"],
        cwd="/app",
    )
    if not ok:
        return False, f"Could not create gateway container: {err}"
    return True, "Starting IB Gateway. Approve 2FA on your phone if prompted."


def trigger_gateway_login() -> tuple[bool, str]:
    if not _docker_socket_available():
        return False, "Docker is not available. Start ib-gateway manually: docker compose --profile ibkr up -d ib-gateway"

    state = get_gateway_container_state()
    if state == "missing":
        return _create_gateway_container()

    if state == "exited":
        ok, err = _run_docker_command(["start", _container_name()])
        if not ok:
            return False, f"Could not start gateway container: {err}"
        return True, "Starting IB Gateway. Approve 2FA on your phone if prompted."

    if state == "running":
        if is_ibkr_connected():
            return True, "Already connected to IBKR."
        ok, err = _run_docker_command(["restart", _container_name()])
        if not ok:
            return False, f"Could not restart gateway container: {err}"
        return True, "Restarting IB Gateway. Approve 2FA on your phone if prompted."

    return False, "Could not determine gateway container state."


async def resolve_ibkr_status() -> tuple[IbkrStatus, str, bool]:
    if is_ibkr_connected():
        return "connected", "Connected to IBKR.", True

    container_state = get_gateway_container_state()
    gateway_running = container_state == "running"

    if container_state in ("unavailable", "missing", "exited"):
        if container_state == "exited":
            return "error", "IB Gateway container has stopped.", False
        return "disconnected", "Not connected to IBKR.", False

    port_open = await is_api_port_open()
    if gateway_running and not port_open:
        return "connecting", "Approve 2FA on your phone if prompted.", True

    if gateway_running:
        return "connecting", "Connecting to IB Gateway...", True

    return "disconnected", "Not connected to IBKR.", False
