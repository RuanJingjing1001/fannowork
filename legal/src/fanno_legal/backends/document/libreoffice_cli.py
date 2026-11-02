from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

from fanno_work.backends import BackendFailure, execution_evidence


class LibreOfficeFailure(BackendFailure):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(
            code,
            detail,
            backend="libreoffice-headless",
            timed_out=code == "conversion_timeout",
            retryable=code == "conversion_timeout",
        )


@dataclass(frozen=True)
class LibreOfficeConversion:
    generated_path: Path
    backend: str
    backend_version: str
    stdout: str
    stderr: str
    execution_evidence: dict[str, Any] = field(
        default_factory=dict
    )


class LibreOfficeBackend:
    """Execute document conversion using real LibreOffice."""

    def __init__(
        self,
        *,
        executable: str,
        timeout_seconds: int,
    ) -> None:
        if not isinstance(executable, str) or not executable:
            raise ValueError(
                "libreoffice executable must be a non-empty string"
            )
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError(
                "libreoffice timeout_seconds "
                "must be a positive integer"
            )

        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def convert(
        self,
        *,
        source: Path,
        output_dir: Path,
        output_format: str,
    ) -> LibreOfficeConversion:
        source = Path(source).resolve()
        output_dir = Path(output_dir).resolve()

        output_dir.mkdir(parents=True, exist_ok=True)

        profile_dir = output_dir / ".libreoffice-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        command = [
            self.executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            (
                "-env:UserInstallation="
                f"{profile_dir.as_uri()}"
            ),
            "--convert-to",
            output_format,
            "--outdir",
            str(output_dir),
            str(source),
        ]

        stdout, stderr = self._execute(command)

        generated_path = (
            output_dir
            / f"{source.stem}.{output_format}"
        )

        if not generated_path.is_file():
            raise LibreOfficeFailure(
                "conversion_output_missing"
            )

        return LibreOfficeConversion(
            generated_path=generated_path,
            backend="libreoffice-headless",
            backend_version=self._read_version(),
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
            execution_evidence=execution_evidence(
                backend="libreoffice-headless",
                execution_scope="controller_process",
                timeout_seconds=self.timeout_seconds,
                runtime="subprocess",
            ),
        )

    def _execute(
        self,
        command: list[str],
    ) -> tuple[str, str]:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise LibreOfficeFailure(
                "backend_unavailable"
            ) from exc
        except OSError as exc:
            raise LibreOfficeFailure(
                "conversion_failed"
            ) from exc

        try:
            stdout, stderr = process.communicate(
                timeout=self.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(
                    process.pid,
                    signal.SIGKILL,
                )
            except ProcessLookupError:
                pass

            process.communicate()

            raise LibreOfficeFailure(
                "conversion_timeout"
            ) from exc

        if process.returncode != 0:
            raise LibreOfficeFailure(
                "conversion_failed"
            )

        return stdout, stderr

    def _read_version(self) -> str:
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "--version",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
        except (
            FileNotFoundError,
            OSError,
            subprocess.TimeoutExpired,
        ):
            return "unknown"

        if completed.returncode != 0:
            return "unknown"

        version = completed.stdout.strip()
        return version or "unknown"
