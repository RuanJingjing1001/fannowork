from __future__ import annotations

import hashlib
import imaplib
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any

from fanno_work.backends import (
    execution_evidence,
)

from .greenmail import (
    EmailAccount,
    EmailBackendFailure,
    EmailSeed,
    GreenMailClient,
)


class DirectGreenMailBackend:
    backend_id = "greenmail"

    def __init__(
        self,
        *,
        workspace: Path,
        account: EmailAccount,
        host: str,
        imap_port: int,
        smtp_port: int,
        timeout_seconds: int,
    ) -> None:
        self.workspace = Path(
            workspace
        ).resolve()
        self.account = account
        self.host = host
        self.imap_port = imap_port
        self.smtp_port = smtp_port
        self.timeout_seconds = (
            timeout_seconds
        )

    def _client(self) -> GreenMailClient:
        return GreenMailClient(
            host=self.host,
            imap_port=self.imap_port,
            smtp_port=self.smtp_port,
            account=self.account,
            timeout_seconds=(
                self.timeout_seconds
            ),
        )

    def _evidence(self) -> dict[str, Any]:
        return execution_evidence(
            backend=self.backend_id,
            execution_scope="task_container",
            timeout_seconds=(
                self.timeout_seconds
            ),
            runtime="docker",
        )

    def search(
        self,
        *,
        query: str | None,
        sender: str | None,
        subject: str | None,
        max_results: int,
    ) -> dict[str, Any]:
        messages = self._client().search(
            query=query,
            sender=sender,
            subject=subject,
            max_results=max_results,
        )

        return {
            "messages": messages,
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def read(
        self,
        *,
        message_id: str,
    ) -> dict[str, Any]:
        message = self._client().read(
            message_id
        )

        return {
            **message,
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def send(
        self,
        *,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        result = self._client().send(
            recipients=to,
            cc=cc,
            subject=subject,
            body=body,
        )

        return {
            **result,
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def download_attachment(
        self,
        *,
        message_id: str,
        attachment_id: str,
        target_path: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        relative, target = (
            self._resolve_target(
                target_path
            )
        )

        attachment = (
            self._client().get_attachment(
                message_id=message_id,
                attachment_id=attachment_id,
                max_bytes=max_bytes,
            )
        )

        content = attachment.pop(
            "content"
        )

        if not isinstance(content, bytes):
            raise EmailBackendFailure(
                "invalid_backend_response",
                "attachment content is invalid",
            )

        existed = target.is_file()
        changed = True

        if (
            existed
            and target.read_bytes() == content
        ):
            changed = False

        if changed:
            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temporary_path: Path | None = None

            try:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    delete=False,
                ) as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary_path = Path(
                        handle.name
                    )

                os.replace(
                    temporary_path,
                    target,
                )

            except OSError as exc:
                if temporary_path is not None:
                    temporary_path.unlink(
                        missing_ok=True
                    )

                raise EmailBackendFailure(
                    "write_failed",
                    str(exc),
                ) from exc

        return {
            **attachment,
            "path": relative,
            "created": not existed,
            "changed": changed,
            "size": len(content),
            "sha256": hashlib.sha256(
                content
            ).hexdigest(),
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def _resolve_target(
        self,
        value: str,
    ) -> tuple[str, Path]:
        if (
            not value
            or "\\" in value
        ):
            raise EmailBackendFailure(
                "invalid_path",
                value,
            )

        pure = PurePosixPath(value)

        if (
            pure.is_absolute()
            or ".." in pure.parts
            or pure.as_posix() == "."
        ):
            raise EmailBackendFailure(
                "invalid_path",
                value,
            )

        target = self.workspace.joinpath(
            *pure.parts
        ).resolve()

        if not target.is_relative_to(
            self.workspace
        ):
            raise EmailBackendFailure(
                "permission_denied",
                value,
            )

        current = self.workspace

        for part in pure.parts:
            current = current / part

            if (
                current.exists()
                and current.is_symlink()
            ):
                raise EmailBackendFailure(
                    "permission_denied",
                    value,
                )

        return pure.as_posix(), target


def clear_greenmail(
    *,
    seed: EmailSeed,
    host: str,
    imap_port: int,
    timeout_seconds: int,
) -> None:
    for account in seed.accounts.values():
        connection: (
            imaplib.IMAP4 | None
        ) = None

        try:
            connection = imaplib.IMAP4(
                host,
                imap_port,
                timeout=timeout_seconds,
            )
            connection.login(
                account.address,
                account.password,
            )
            connection.select("INBOX")

            status, data = (
                connection.uid(
                    "search",
                    None,
                    "ALL",
                )
            )

            if status != "OK":
                raise EmailBackendFailure(
                    "email_reset_failed",
                    account.address,
                )

            identifiers = (
                data[0].split()
                if data and data[0]
                else []
            )

            for identifier in identifiers:
                connection.uid(
                    "store",
                    identifier,
                    "+FLAGS.SILENT",
                    r"(\Deleted)",
                )

            connection.expunge()

        except EmailBackendFailure:
            raise

        except (
            OSError,
            imaplib.IMAP4.error,
        ) as exc:
            raise EmailBackendFailure(
                "email_reset_failed",
                str(exc),
                retryable=True,
            ) from exc

        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

                try:
                    connection.logout()
                except Exception:
                    pass