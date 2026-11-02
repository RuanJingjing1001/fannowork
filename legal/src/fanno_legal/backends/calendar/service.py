from __future__ import annotations

import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from fanno_work.backends import BackendFailure


class RadicaleServiceFailure(BackendFailure):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(
            code,
            detail,
            backend="radicale-service",
            retryable=code in {
                "radicale_not_ready",
                "radicale_start_failed",
                "radicale_docker_failed",
            },
        )


class RadicaleService:
    CALDAV_PORT = 5232

    def __init__(
        self,
        *,
        image: str,
        docker_executable: str = "docker",
        startup_timeout_seconds: int = 60,
        network_name: str | None = None,
    ) -> None:
        if not isinstance(image, str) or not image:
            raise ValueError("Radicale image must be non-empty")
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        self.image = image
        self.docker_executable = docker_executable
        self.startup_timeout_seconds = startup_timeout_seconds
        self.network_name = network_name
        self._owns_network = network_name is None
        self.container_name: str | None = None
        self.host_port: int | None = None

    @property
    def running(self) -> bool:
        return self.container_name is not None

    @property
    def host_url(self) -> str:
        if self.host_port is None:
            raise RadicaleServiceFailure("radicale_not_started")
        return f"http://127.0.0.1:{self.host_port}"

    def start(self) -> None:
        if self.running:
            raise RadicaleServiceFailure("radicale_already_started")
        self._check_engine()
        self._check_image()
        if self.network_name is None:
            self._create_network()

        name = f"fanno-radicale-{uuid.uuid4().hex[:12]}"
        result = self._run(
            [
                self.docker_executable,
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--network",
                str(self.network_name),
                "--network-alias",
                "radicale",
                "--publish",
                "127.0.0.1::5232",
                "--env",
                "RADICALE_CONFIG_AUTH_TYPE=none",
                "--env",
                "RADICALE_CONFIG_SERVER_HOSTS=0.0.0.0:5232",
                "--env",
                "RADICALE_CONFIG_RIGHTS_TYPE=authenticated",
                self.image,
            ],
            timeout=60,
        )
        if result.returncode != 0:
            raise RadicaleServiceFailure(
                "radicale_start_failed",
                result.stderr.strip() or result.stdout.strip(),
            )
        self.container_name = name
        try:
            self.host_port = self._host_port()
            self._wait_until_ready()
        except Exception:
            self._remove_container()
            raise

    def restart(self) -> None:
        self._remove_container()
        self.start()

    def stop(self) -> None:
        self._remove_container()
        network = self.network_name
        if not self._owns_network:
            return
        self.network_name = None
        if network is not None:
            self._run(
                [self.docker_executable, "network", "rm", network],
                timeout=30,
            )

    def _create_network(self) -> None:
        network = f"fanno-calendar-{uuid.uuid4().hex[:12]}"
        result = self._run(
            [
                self.docker_executable,
                "network",
                "create",
                "--driver",
                "bridge",
                network,
            ],
            timeout=30,
        )
        if result.returncode != 0:
            raise RadicaleServiceFailure(
                "radicale_network_failed",
                result.stderr.strip() or result.stdout.strip(),
            )
        self.network_name = network

    def _remove_container(self) -> None:
        name = self.container_name
        self.container_name = None
        self.host_port = None
        if name is not None:
            self._run(
                [self.docker_executable, "rm", "--force", name],
                timeout=30,
            )

    def _host_port(self) -> int:
        if self.container_name is None:
            raise RadicaleServiceFailure("radicale_not_started")
        result = self._run(
            [
                self.docker_executable,
                "port",
                self.container_name,
                "5232/tcp",
            ],
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RadicaleServiceFailure(
                "radicale_port_unavailable",
                result.stderr.strip(),
            )
        try:
            return int(result.stdout.splitlines()[0].rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise RadicaleServiceFailure(
                "radicale_port_unavailable",
                result.stdout.strip(),
            ) from exc

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                request = Request(
                    self.host_url,
                    method="GET",
                )
                with urlopen(request, timeout=2) as response:
                    if 200 <= response.status < 500:
                        return
            except HTTPError as exc:
                # An HTTP response proves the application server is ready;
                # authorization is validated by the CalDAV client later.
                if 400 <= exc.code < 500:
                    return
                last_error = f"HTTP {exc.code}"
            except (OSError, URLError) as exc:
                last_error = str(exc)
                time.sleep(0.5)
        raise RadicaleServiceFailure("radicale_not_ready", last_error)

    def _check_engine(self) -> None:
        result = self._run([self.docker_executable, "info"], timeout=15)
        if result.returncode != 0:
            raise RadicaleServiceFailure(
                "container_engine_unavailable",
                result.stderr.strip() or result.stdout.strip(),
            )

    def _check_image(self) -> None:
        result = self._run(
            [self.docker_executable, "image", "inspect", self.image],
            timeout=15,
        )
        if result.returncode != 0:
            raise RadicaleServiceFailure("radicale_image_unavailable", self.image)

    @staticmethod
    def _run(
        command: list[str],
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RadicaleServiceFailure("radicale_docker_failed", str(exc)) from exc
