from __future__ import annotations

import json
from typing import Any

from fanno_work.sandbox import (
    ContainerWorkspace,
    ContainerWorkspaceError,
)

from .greenmail import (
    EmailAccount,
    EmailBackendFailure,
)


class ContainerGreenMailBackend:
    backend_id = "greenmail"

    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        account: EmailAccount,
        host: str = "greenmail",
        imap_port: int = 3143,
        smtp_port: int = 3025,
        timeout_seconds: int = 15,
        attachment_writable_prefixes: tuple[
            str,
            ...,
        ] = ("work/email-attachments/",),
        worker_path: str = (
            "/opt/fanno/workers/email_worker.py"
        ),
        python_executable: str = "python",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        self.workspace = workspace
        self.account = account
        self.host = host
        self.imap_port = imap_port
        self.smtp_port = smtp_port
        self.timeout_seconds = timeout_seconds
        if not attachment_writable_prefixes:
            raise ValueError(
                "attachment_writable_prefixes "
                "must not be empty"
            )
        self.attachment_writable_prefixes = (
            attachment_writable_prefixes
        )
        self.worker_path = worker_path
        self.python_executable = (
            python_executable
        )

    def search(
        self,
        *,
        query: str | None,
        sender: str | None,
        subject: str | None,
        max_results: int,
    ) -> dict[str, Any]:
        return self._call(
            {
                "operation": "search",
                "query": query,
                "sender": sender,
                "subject": subject,
                "max_results": max_results,
            }
        )

    def read(
        self,
        *,
        message_id: str,
    ) -> dict[str, Any]:
        return self._call(
            {
                "operation": "read",
                "message_id": message_id,
            }
        )

    def send(
        self,
        *,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        return self._call(
            {
                "operation": "send",
                "to": to,
                "cc": cc,
                "subject": subject,
                "body": body,
            }
        )

    def download_attachment(
        self,
        *,
        message_id: str,
        attachment_id: str,
        target_path: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        return self._call(
            {
                "operation": (
                    "download_attachment"
                ),
                "message_id": message_id,
                "attachment_id": (
                    attachment_id
                ),
                "target_path": target_path,
                "max_attachment_bytes": (
                    max_bytes
                ),
                "attachment_writable_prefixes": (
                    list(
                        self
                        .attachment_writable_prefixes
                    )
                ),
            }
        )

    def _call(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        request = {
            **arguments,
            "host": self.host,
            "imap_port": self.imap_port,
            "smtp_port": self.smtp_port,
            "timeout_seconds": (
                self.timeout_seconds
            ),
            "account_id": self.account.id,
            "account_address": (
                self.account.address
            ),
            "account_password": (
                self.account.password
            ),
            "account_display_name": (
                self.account.display_name
            ),
        }

        try:
            completed = self.workspace.execute(
                [
                    self.python_executable,
                    self.worker_path,
                ],
                input_text=json.dumps(
                    request,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                timeout_seconds=(
                    self.timeout_seconds + 5
                ),
            )
        except ContainerWorkspaceError as exc:
            timed_out = (
                exc.code == "container_timeout"
            )

            raise EmailBackendFailure(
                (
                    "email_timeout"
                    if timed_out
                    else "email_backend_unavailable"
                ),
                exc.detail or exc.code,
                timed_out=timed_out,
                retryable=True,
            ) from exc

        try:
            payload = json.loads(
                completed.stdout
            )
        except json.JSONDecodeError as exc:
            raise EmailBackendFailure(
                "invalid_backend_response",
                completed.stdout[-2000:],
            ) from exc

        if not isinstance(payload, dict):
            raise EmailBackendFailure(
                "invalid_backend_response"
            )

        if (
            completed.return_code != 0
            or payload.get("ok") is not True
        ):
            backend_error = payload.get(
                "backend_error",
                {},
            )

            detail = (
                str(
                    backend_error.get(
                        "detail",
                        "",
                    )
                )
                if isinstance(
                    backend_error,
                    dict,
                )
                else completed.stderr[-2000:]
            )

            raise EmailBackendFailure(
                str(
                    payload.get(
                        "error",
                        "email_backend_failed",
                    )
                ),
                detail,
            )

        output = payload.get("output")
        evidence = payload.get(
            "execution_evidence"
        )

        if (
            not isinstance(output, dict)
            or not isinstance(evidence, dict)
        ):
            raise EmailBackendFailure(
                "invalid_backend_response"
            )

        return {
            **output,
            "backend": self.backend_id,
            "execution_evidence": dict(
                evidence
            ),
        }
