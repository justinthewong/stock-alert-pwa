from __future__ import annotations

import logging
import os
import re
import socket
from typing import Any, Literal

import httpx
import yaml

logger = logging.getLogger(__name__)

ContainerState = Literal["running", "exited", "missing", "unavailable"]
DEFAULT_SOCKET = "/var/run/docker.sock"
MIN_API_VERSION = (1, 44)


def _parse_api_version(value: str) -> tuple[int, int]:
    major, _, minor = value.lstrip("v").partition(".")
    return int(major), int(minor or 0)


def _format_api_version(major: int, minor: int) -> str:
    return f"v{major}.{minor}"


def _resolve_api_version(socket_path: str) -> str:
    configured = os.getenv("DOCKER_API_VERSION", "").strip()
    if configured:
        return configured if configured.startswith("v") else f"v{configured}"

    probe = httpx.Client(
        transport=httpx.HTTPTransport(uds=socket_path),
        base_url="http://docker",
        timeout=5.0,
    )
    try:
        response = probe.get("/version")
        if response.status_code == 200:
            payload = response.json()
            negotiated = _parse_api_version(str(payload.get("ApiVersion", "1.44")))
            minimum = _parse_api_version(str(payload.get("MinAPIVersion", "1.44")))
            chosen = max(negotiated, minimum, MIN_API_VERSION)
            return _format_api_version(*chosen)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("Could not negotiate Docker API version: %s", exc)
    finally:
        probe.close()

    return _format_api_version(*MIN_API_VERSION)


class DockerSocketError(Exception):
    def __init__(self, message: str, *, details: str = "") -> None:
        super().__init__(message)
        self.details = details


class DockerSocketClient:
    def __init__(self, socket_path: str = DEFAULT_SOCKET) -> None:
        self.socket_path = socket_path
        self.api_version = _resolve_api_version(socket_path)
        self._client = httpx.Client(
            transport=httpx.HTTPTransport(uds=socket_path),
            base_url=f"http://docker/{self.api_version}",
            timeout=60.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DockerSocketClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def available(self) -> tuple[bool, str]:
        if not os.path.exists(self.socket_path):
            return False, f"Docker socket not found at {self.socket_path}"
        try:
            response = self._client.get("/version")
        except (httpx.HTTPError, OSError) as exc:
            return False, str(exc)
        if response.status_code != 200:
            return False, response.text.strip() or f"Docker API returned {response.status_code}"
        return True, f"Docker API {self.api_version}"

    def container_state(self, name: str) -> ContainerState:
        try:
            response = self._client.get(f"/containers/{name}/json")
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Docker inspect failed for %s: %s", name, exc)
            return "unavailable"

        if response.status_code == 404:
            return "missing"
        if response.status_code != 200:
            logger.warning("Docker inspect failed for %s: %s", name, response.text.strip())
            return "unavailable"

        payload = response.json()
        if payload.get("State", {}).get("Running"):
            return "running"
        return "exited"

    def start_container(self, name: str) -> str:
        response = self._client.post(f"/containers/{name}/start")
        if response.status_code in (204, 304):
            return f"Started container {name}."
        raise DockerSocketError(
            f"Could not start container {name}.",
            details=response.text.strip() or f"HTTP {response.status_code}",
        )

    def restart_container(self, name: str) -> str:
        response = self._client.post(f"/containers/{name}/restart")
        if response.status_code == 204:
            return f"Restarted container {name}."
        raise DockerSocketError(
            f"Could not restart container {name}.",
            details=response.text.strip() or f"HTTP {response.status_code}",
        )

    def _app_network_name(self) -> str:
        hostname = socket.gethostname()
        response = self._client.get(f"/containers/{hostname}/json")
        if response.status_code == 200:
            networks = response.json().get("NetworkSettings", {}).get("Networks", {})
            if networks:
                return next(iter(networks.keys()))

        project_name = os.getenv("COMPOSE_PROJECT_NAME", "stock-alert")
        return f"{project_name}_default"

    def _load_env_file(self, path: str) -> dict[str, str]:
        if not os.path.isfile(path):
            return {}

        values: dict[str, str] = {}
        with open(path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        return values

    def _resolve_template(self, value: str, env: dict[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            token = match.group(1)
            if ":-" in token:
                key, default = token.split(":-", 1)
                return env.get(key, default)
            return env.get(token, "")

        return re.sub(r"\$\{([^}]+)\}", replace, value)

    def _gateway_env(self) -> list[str]:
        compose_path = os.getenv("COMPOSE_FILE", "/app/docker-compose.yml")
        env_values = self._load_env_file("/app/.env")
        if not os.path.exists(compose_path):
            raise DockerSocketError(f"Compose file not found at {compose_path}.")

        with open(compose_path, encoding="utf-8") as handle:
            compose_data = yaml.safe_load(handle) or {}

        service_env = compose_data.get("services", {}).get("ib-gateway", {}).get("environment", {})
        if not isinstance(service_env, dict):
            raise DockerSocketError("ib-gateway environment is not configured in docker-compose.yml.")

        resolved: list[str] = []
        for key, value in service_env.items():
            resolved_value = self._resolve_template(str(value), env_values)
            resolved.append(f"{key}={resolved_value}")
        return resolved

    def _gateway_image(self) -> str:
        compose_path = os.getenv("COMPOSE_FILE", "/app/docker-compose.yml")
        with open(compose_path, encoding="utf-8") as handle:
            compose_data = yaml.safe_load(handle) or {}
        image = compose_data.get("services", {}).get("ib-gateway", {}).get("image")
        if not image:
            raise DockerSocketError("ib-gateway image is not configured in docker-compose.yml.")
        return str(image)

    def create_gateway_container(self, name: str) -> list[str]:
        image = self._gateway_image()
        env = self._gateway_env()
        network = self._app_network_name()
        logs = [
            f"Using Docker network: {network}",
            f"Pulling image if needed: {image}",
        ]

        repo, _, tag = image.partition(":")
        if not tag:
            repo, tag = image, "latest"
        pull = self._client.post("/images/create", params={"fromImage": repo, "tag": tag})
        if pull.status_code not in (200, 201):
            raise DockerSocketError(
                f"Could not pull image {image}.",
                details=pull.text.strip() or f"HTTP {pull.status_code}",
            )

        create_body: dict[str, Any] = {
            "Image": image,
            "Env": env,
            "HostConfig": {
                "PortBindings": {
                    "4003/tcp": [{"HostIp": "127.0.0.1", "HostPort": "4001"}],
                    "4004/tcp": [{"HostIp": "127.0.0.1", "HostPort": "4002"}],
                },
                "RestartPolicy": {"Name": "unless-stopped"},
            },
            "NetworkingConfig": {
                "EndpointsConfig": {
                    network: {},
                }
            },
        }

        logs.append(f"Creating container {name}...")
        response = self._client.post(f"/containers/create", params={"name": name}, json=create_body)
        if response.status_code not in (200, 201):
            raise DockerSocketError(
                f"Could not create container {name}.",
                details=response.text.strip() or f"HTTP {response.status_code}",
            )

        logs.append(self.start_container(name))
        logs.append("IB Gateway container started. Waiting for login and 2FA approval...")
        return logs


def get_docker_client() -> DockerSocketClient | None:
    if not os.path.exists(DEFAULT_SOCKET):
        return None
    return DockerSocketClient()
