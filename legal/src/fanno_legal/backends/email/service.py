from __future__ import annotations

import imaplib
import smtplib
import subprocess
import time
import uuid

from fanno_work.backends import BackendFailure


class GreenMailServiceFailure(BackendFailure):
    def __init__(
        self,
        code: str,
        detail: str = "",
    ) -> None:
        super().__init__(
            code,
            detail,
            backend="greenmail-service",
            retryable=code in {
                "greenmail_not_ready",
                "greenmail_start_failed",
                "greenmail_docker_failed",
            },
        )


class GreenMailService:
    SMTP_PORT = 3025
    IMAP_PORT = 3143
    API_PORT = 8080

    def __init__(
        self,
        *,
        image: str,
        docker_executable: str = "docker",
        startup_timeout_seconds: int = 60,
    ) -> None:
        if not isinstance(image, str) or not image:
            raise ValueError(
                "GreenMail image must be non-empty"
            )

        if (
            not isinstance(
                startup_timeout_seconds,
                int,
            )
            or isinstance(
                startup_timeout_seconds,
                bool,
            )
            or startup_timeout_seconds <= 0
        ):
            raise ValueError(
                "startup_timeout_seconds "
                "must be positive"
            )

        self.image = image
        self.docker_executable = (
            docker_executable
        )
        self.startup_timeout_seconds = (
            startup_timeout_seconds
        )

        self.network_name: str | None = None
        self.container_name: str | None = None
        self.smtp_host_port: int | None = None
        self.imap_host_port: int | None = None
        self.api_host_port: int | None = None

    @property
    def running(self) -> bool:
        return self.container_name is not None

    def start(self) -> None:
        if self.running:
            raise GreenMailServiceFailure(
                "greenmail_already_started"
            )

        self._check_engine()
        self._check_image()

        if self.network_name is None:
            self._create_network()

        container_name = (
            f"fanno-greenmail-"
            f"{uuid.uuid4().hex[:12]}"
        )

        result = self._run(
            [
                self.docker_executable,
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--network",
                self.network_name,
                "--network-alias",
                "greenmail",
                "--publish",
                "127.0.0.1::3025",
                "--publish",
                "127.0.0.1::3143",
                "--publish",
                "127.0.0.1::8080",
                "--env",
                (
                    "GREENMAIL_OPTS="
                    "-Dgreenmail.setup.test.all "
                    "-Dgreenmail.hostname=0.0.0.0 "
                    "-Dgreenmail.auth.disabled"
                ),
                self.image,
            ],
            timeout=60,
        )

        if result.returncode != 0:
            raise GreenMailServiceFailure(
                "greenmail_start_failed",
                (
                    result.stderr.strip()
                    or result.stdout.strip()
                ),
            )

        self.container_name = container_name

        try:
            self.smtp_host_port = (
                self._host_port(
                    self.SMTP_PORT
                )
            )
            self.imap_host_port = (
                self._host_port(
                    self.IMAP_PORT
                )
            )
            self.api_host_port = (
                self._host_port(
                    self.API_PORT
                )
            )

            self._wait_until_ready()
        except Exception:
            self._remove_container()
            raise

    def restart(self) -> None:
        self._remove_container()
        self.start()

    def stop(self) -> None:
        self._remove_container()

        network_name = self.network_name
        self.network_name = None

        if network_name is None:
            return

        self._run(
            [
                self.docker_executable,
                "network",
                "rm",
                network_name,
            ],
            timeout=30,
        )

    def _create_network(self) -> None:
        network_name = (
            f"fanno-mail-"
            f"{uuid.uuid4().hex[:12]}"
        )

        result = self._run(
            [
                self.docker_executable,
                "network",
                "create",
                "--driver",
                "bridge",
                network_name,
            ],
            timeout=30,
        )

        if result.returncode != 0:
            raise GreenMailServiceFailure(
                "greenmail_network_failed",
                (
                    result.stderr.strip()
                    or result.stdout.strip()
                ),
            )

        self.network_name = network_name

    def _remove_container(self) -> None:
        container_name = self.container_name

        self.container_name = None
        self.smtp_host_port = None
        self.imap_host_port = None
        self.api_host_port = None

        if container_name is None:
            return

        self._run(
            [
                self.docker_executable,
                "rm",
                "--force",
                container_name,
            ],
            timeout=30,
        )

    def _host_port(
        self,
        container_port: int,
    ) -> int:
        if self.container_name is None:
            raise GreenMailServiceFailure(
                "greenmail_not_started"
            )

        result = self._run(
            [
                self.docker_executable,
                "port",
                self.container_name,
                f"{container_port}/tcp",
            ],
            timeout=15,
        )

        if result.returncode != 0:
            raise GreenMailServiceFailure(
                "greenmail_port_unavailable",
                (
                    result.stderr.strip()
                    or result.stdout.strip()
                ),
            )

        lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

        if not lines:
            raise GreenMailServiceFailure(
                "greenmail_port_unavailable",
                f"no mapping for {container_port}",
            )

        try:
            return int(
                lines[0].rsplit(":", 1)[1]
            )
        except (
            IndexError,
            ValueError,
        ) as exc:
            raise GreenMailServiceFailure(
                "greenmail_port_unavailable",
                lines[0],
            ) from exc

    def _wait_until_ready(self) -> None:
        deadline = (
            time.monotonic()
            + self.startup_timeout_seconds
        )

        last_error = ""

        while time.monotonic() < deadline:
            try:
                self._verify_smtp()
                self._verify_imap()
                return
            except (
                OSError,
                TimeoutError,
                smtplib.SMTPException,
                imaplib.IMAP4.error,
                imaplib.IMAP4.abort,
            ) as exc:
                last_error = (
                    f"{type(exc).__name__}: {exc}"
                )
                time.sleep(0.5)

        raise GreenMailServiceFailure(
            "greenmail_not_ready",
            last_error,
        )

    def _verify_smtp(self) -> None:
        port = self.smtp_host_port

        if port is None:
            raise OSError(
                "SMTP host port is unavailable"
            )

        connection: smtplib.SMTP | None = None

        try:
            connection = smtplib.SMTP(
                "127.0.0.1",
                port,
                timeout=2,
            )

            code, _ = connection.noop()

            if code != 250:
                raise smtplib.SMTPException(
                    "unexpected SMTP NOOP "
                    f"response: {code}"
                )
        finally:
            if connection is not None:
                try:
                    connection.quit()
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass

    def _verify_imap(self) -> None:
        port = self.imap_host_port

        if port is None:
            raise OSError(
                "IMAP host port is unavailable"
            )

        connection: imaplib.IMAP4 | None = None

        try:
            connection = imaplib.IMAP4(
                "127.0.0.1",
                port,
                timeout=2,
            )

            status, _ = connection.noop()

            if status != "OK":
                raise imaplib.IMAP4.error(
                    "IMAP NOOP did not return OK"
                )
        finally:
            if connection is not None:
                try:
                    connection.logout()
                except Exception:
                    try:
                        connection.shutdown()
                    except Exception:
                        pass

    def _check_engine(self) -> None:
        result = self._run(
            [
                self.docker_executable,
                "info",
            ],
            timeout=15,
        )

        if result.returncode != 0:
            raise GreenMailServiceFailure(
                "container_engine_unavailable",
                (
                    result.stderr.strip()
                    or result.stdout.strip()
                ),
            )

    def _check_image(self) -> None:
        result = self._run(
            [
                self.docker_executable,
                "image",
                "inspect",
                self.image,
            ],
            timeout=15,
        )

        if result.returncode != 0:
            raise GreenMailServiceFailure(
                "greenmail_image_unavailable",
                self.image,
            )

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
        except subprocess.TimeoutExpired as exc:
            raise GreenMailServiceFailure(
                "greenmail_docker_failed",
                (
                    "command timed out: "
                    + " ".join(command[:3])
                ),
            ) from exc
        except OSError as exc:
            raise GreenMailServiceFailure(
                "greenmail_docker_failed",
                str(exc),
            ) from exc