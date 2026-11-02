from __future__ import annotations

from email.header import decode_header
from email.parser import BytesParser
from email.policy import default
import hashlib
import imaplib
import json
import re
from typing import Any

from .greenmail import (
    EmailAccount,
    EmailBackendFailure,
    EmailSeed,
)


_LIST_PATTERN = re.compile(
    rb"^\((?P<attributes>[^)]*)\)\s+"
    rb"(?P<delimiter>NIL|\"(?:\\.|[^\"])*\")\s+"
    rb"(?P<name>.+)$"
)

_FLAGS_PATTERN = re.compile(
    rb"FLAGS\s+\((?P<flags>[^)]*)\)",
    re.IGNORECASE,
)


def _decode_header(value: str) -> str:
    parts: list[str] = []

    for content, charset in decode_header(value):
        if isinstance(content, bytes):
            parts.append(
                content.decode(
                    charset or "utf-8",
                    errors="replace",
                )
            )
        else:
            parts.append(content)

    return "".join(parts)


def _decode_folder_name(value: bytes) -> str:
    value = value.strip()

    if (
        len(value) >= 2
        and value.startswith(b'"')
        and value.endswith(b'"')
    ):
        value = value[1:-1]
        value = value.replace(
            b'\\"',
            b'"',
        )
        value = value.replace(
            b"\\\\",
            b"\\",
        )

    return value.decode(
        "utf-8",
        errors="replace",
    )


def _quote_folder(value: str) -> str:
    escaped = value.replace(
        "\\",
        "\\\\",
    ).replace(
        '"',
        '\\"',
    )

    return f'"{escaped}"'


def _normalized_flags(
    metadata: bytes,
) -> list[str]:
    match = _FLAGS_PATTERN.search(metadata)

    if match is None:
        return []

    flags = [
        item.decode(
            "ascii",
            errors="replace",
        )
        for item in match.group(
            "flags"
        ).split()
    ]

    # \Recent depends on the IMAP session and is
    # unsuitable for deterministic snapshots.
    flags = [
        item
        for item in flags
        if item.casefold() != "\\recent"
    ]

    return sorted(
        flags,
        key=str.casefold,
    )


def _decoded_text(
    part: Any,
) -> str:
    payload = part.get_payload(
        decode=True
    ) or b""

    charset = (
        part.get_content_charset()
        or "utf-8"
    )

    return payload.decode(
        charset,
        errors="replace",
    )


def _message_summary(
    raw_message: bytes,
    flags: list[str],
) -> dict[str, Any]:
    parsed = BytesParser(
        policy=default
    ).parsebytes(raw_message)

    plain_text: str | None = None
    html_text: str | None = None
    attachments: list[dict[str, Any]] = []

    if parsed.is_multipart():
        for part_index, part in enumerate(
            parsed.walk()
        ):
            if part.is_multipart():
                continue

            disposition = (
                part.get_content_disposition()
            )
            filename = part.get_filename()
            payload = part.get_payload(
                decode=True
            ) or b""

            if (
                disposition == "attachment"
                or filename
            ):
                attachments.append(
                    {
                        "attachment_id": (
                            f"part-{part_index}"
                        ),
                        "filename": (
                            filename
                            or "attachment"
                        ),
                        "content_type": (
                            part.get_content_type()
                        ),
                        "size": len(payload),
                        "sha256": (
                            hashlib.sha256(
                                payload
                            ).hexdigest()
                        ),
                    }
                )
                continue

            content_type = (
                part.get_content_type()
            )

            if (
                content_type == "text/plain"
                and plain_text is None
            ):
                plain_text = _decoded_text(
                    part
                )

            if (
                content_type == "text/html"
                and html_text is None
            ):
                html_text = _decoded_text(
                    part
                )
    else:
        content_type = (
            parsed.get_content_type()
        )
        text = _decoded_text(parsed)

        if content_type == "text/html":
            html_text = text
        else:
            plain_text = text

    if plain_text is not None:
        body = plain_text
        body_content_type = "text/plain"
    elif html_text is not None:
        body = html_text
        body_content_type = "text/html"
    else:
        body = ""
        body_content_type = (
            parsed.get_content_type()
        )

    body_bytes = body.encode("utf-8")

    message_id_header = parsed.get(
        "Message-ID"
    )

    if message_id_header:
        message_id = str(
            message_id_header
        )
    else:
        identity_payload = {
            "from": str(
                parsed.get("From", "")
            ),
            "to": str(
                parsed.get("To", "")
            ),
            "cc": str(
                parsed.get("Cc", "")
            ),
            "subject": str(
                parsed.get("Subject", "")
            ),
            "date": str(
                parsed.get("Date", "")
            ),
            "body_sha256": hashlib.sha256(
                body_bytes
            ).hexdigest(),
            "attachments": attachments,
        }

        encoded_identity = json.dumps(
            identity_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        digest = hashlib.sha256(
            encoded_identity
        ).hexdigest()[:24]

        message_id = (
            f"<snapshot-{digest}@fanno.local>"
        )

    references_header = str(
        parsed.get("References", "")
    )

    return {
        "message_id": message_id,
        "from": _decode_header(
            str(parsed.get("From", ""))
        ),
        "to": _decode_header(
            str(parsed.get("To", ""))
        ),
        "cc": _decode_header(
            str(parsed.get("Cc", ""))
        ),
        "subject": _decode_header(
            str(parsed.get("Subject", ""))
        ),
        "date": str(
            parsed.get("Date", "")
        ),
        "in_reply_to": str(
            parsed.get("In-Reply-To", "")
        ),
        "references": [
            item
            for item in references_header.split()
            if item
        ],
        "flags": flags,
        "body_content_type": (
            body_content_type
        ),
        "body_size": len(body_bytes),
        "body_sha256": hashlib.sha256(
            body_bytes
        ).hexdigest(),
        "attachments": attachments,
    }


class GreenMailMailboxSnapshotter:
    """Trusted IMAP reader for evaluator-visible state."""

    def __init__(
        self,
        *,
        host: str,
        imap_port: int,
        accounts: dict[
            str,
            EmailAccount,
        ],
        timeout_seconds: int = 15,
    ) -> None:
        self.host = host
        self.imap_port = imap_port
        self.accounts = accounts
        self.timeout_seconds = (
            timeout_seconds
        )

    def snapshot(self) -> dict[str, Any]:
        accounts: dict[
            str,
            dict[str, Any],
        ] = {}

        for account_id in sorted(
            self.accounts
        ):
            account = self.accounts[
                account_id
            ]

            accounts[account_id] = (
                self._snapshot_account(
                    account
                )
            )

        return {
            "schema_version": 1,
            "accounts": accounts,
        }

    def _snapshot_account(
        self,
        account: EmailAccount,
    ) -> dict[str, Any]:
        connection = self._connect(
            account
        )

        try:
            folders: dict[
                str,
                dict[str, Any],
            ] = {}

            for folder in self._list_folders(
                connection
            ):
                folders[folder] = (
                    self._snapshot_folder(
                        connection,
                        folder,
                    )
                )

            return {
                "address": account.address,
                "display_name": (
                    account.display_name
                ),
                "folders": folders,
            }
        finally:
            self._close(connection)

    def _connect(
        self,
        account: EmailAccount,
    ) -> imaplib.IMAP4:
        connection: (
            imaplib.IMAP4 | None
        ) = None

        try:
            connection = imaplib.IMAP4(
                self.host,
                self.imap_port,
                timeout=self.timeout_seconds,
            )

            connection.login(
                account.address,
                account.password,
            )

            return connection
        except TimeoutError as exc:
            if connection is not None:
                self._close(connection)

            raise EmailBackendFailure(
                "email_snapshot_timeout",
                str(exc),
                timed_out=True,
                retryable=True,
            ) from exc
        except (
            OSError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
        ) as exc:
            if connection is not None:
                self._close(connection)

            raise EmailBackendFailure(
                "email_snapshot_failed",
                str(exc),
                retryable=True,
            ) from exc

    @staticmethod
    def _close(
        connection: imaplib.IMAP4,
    ) -> None:
        try:
            connection.logout()
        except Exception:
            try:
                connection.shutdown()
            except Exception:
                pass

    @staticmethod
    def _list_folders(
        connection: imaplib.IMAP4,
    ) -> list[str]:
        status, data = connection.list()

        if status != "OK":
            raise EmailBackendFailure(
                "email_snapshot_failed",
                "IMAP LIST failed",
            )

        folders: set[str] = set()

        for item in data or []:
            if not isinstance(item, bytes):
                continue

            match = _LIST_PATTERN.match(
                item
            )

            if match is None:
                continue

            folders.add(
                _decode_folder_name(
                    match.group("name")
                )
            )

        folders.add("INBOX")

        return sorted(
            folders,
            key=str.casefold,
        )

    def _snapshot_folder(
        self,
        connection: imaplib.IMAP4,
        folder: str,
    ) -> dict[str, Any]:
        status, _ = connection.select(
            _quote_folder(folder),
            readonly=True,
        )

        if status != "OK":
            raise EmailBackendFailure(
                "email_snapshot_failed",
                f"cannot select folder: {folder}",
            )

        status, data = connection.uid(
            "search",
            None,
            "ALL",
        )

        if status != "OK":
            raise EmailBackendFailure(
                "email_snapshot_failed",
                f"cannot search folder: {folder}",
            )

        raw_uids = (
            data[0].split()
            if data and data[0]
            else []
        )

        messages: list[
            dict[str, Any]
        ] = []

        for raw_uid in raw_uids:
            messages.append(
                self._snapshot_message(
                    connection,
                    raw_uid.decode(
                        "ascii"
                    ),
                )
            )

        messages.sort(
            key=lambda item: (
                str(
                    item.get(
                        "message_id",
                        "",
                    )
                ),
                str(
                    item.get(
                        "subject",
                        "",
                    )
                ),
            )
        )

        return {
            "message_count": len(
                messages
            ),
            "messages": messages,
        }

    @staticmethod
    def _snapshot_message(
        connection: imaplib.IMAP4,
        uid: str,
    ) -> dict[str, Any]:
        status, data = connection.uid(
            "fetch",
            uid,
            "(FLAGS BODY.PEEK[])",
        )

        if status != "OK":
            raise EmailBackendFailure(
                "email_snapshot_failed",
                f"cannot fetch UID {uid}",
            )

        metadata = b""
        raw_message: bytes | None = None

        for item in data or []:
            if isinstance(item, tuple):
                if (
                    len(item) >= 1
                    and isinstance(
                        item[0],
                        bytes,
                    )
                ):
                    metadata += item[0]

                if (
                    len(item) >= 2
                    and isinstance(
                        item[1],
                        bytes,
                    )
                ):
                    raw_message = item[1]

            elif isinstance(item, bytes):
                metadata += item

        if raw_message is None:
            raise EmailBackendFailure(
                "email_snapshot_failed",
                f"missing RFC822 for UID {uid}",
            )

        return _message_summary(
            raw_message,
            _normalized_flags(
                metadata
            ),
        )


def snapshot_greenmail(
    *,
    seed: EmailSeed,
    host: str,
    imap_port: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    return GreenMailMailboxSnapshotter(
        host=host,
        imap_port=imap_port,
        accounts=seed.accounts,
        timeout_seconds=timeout_seconds,
    ).snapshot()