from __future__ import annotations

from dataclasses import dataclass
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
import hashlib
import imaplib
import smtplib
from typing import Any, Iterable
import time

from fanno_work.backends import BackendFailure


class EmailBackendFailure(BackendFailure):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        timed_out: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code,
            detail,
            backend="greenmail",
            timed_out=timed_out,
            retryable=retryable,
        )


@dataclass(frozen=True)
class EmailAccount:
    id: str
    address: str
    password: str
    display_name: str


@dataclass(frozen=True)
class EmailSeed:
    accounts: dict[str, EmailAccount]
    messages: tuple[dict[str, Any], ...]


def parse_email_seed(
    initial_state: dict[str, Any],
) -> EmailSeed:
    raw_email = initial_state.get("email", {})

    if not isinstance(raw_email, dict):
        raise ValueError(
            "initial_state.email must be an object"
        )

    raw_accounts = raw_email.get("accounts", {})
    raw_messages = raw_email.get("messages", [])

    if not isinstance(raw_accounts, dict):
        raise ValueError(
            "initial_state.email.accounts must be an object"
        )

    if not isinstance(raw_messages, list):
        raise ValueError(
            "initial_state.email.messages must be a list"
        )

    accounts: dict[str, EmailAccount] = {}

    for account_id, value in raw_accounts.items():
        if (
            not isinstance(account_id, str)
            or not account_id
            or not isinstance(value, dict)
        ):
            raise ValueError("invalid email account")

        address = value.get("address")
        password = value.get("password", "")
        display_name = value.get(
            "display_name",
            address,
        )

        if (
            not isinstance(address, str)
            or "@" not in address
            or not isinstance(password, str)
            or not isinstance(display_name, str)
        ):
            raise ValueError(
                f"invalid email account: {account_id}"
            )

        accounts[account_id] = EmailAccount(
            id=account_id,
            address=address,
            password=password,
            display_name=display_name,
        )

    messages: list[dict[str, Any]] = []

    for index, value in enumerate(raw_messages):
        if not isinstance(value, dict):
            raise ValueError(
                f"email message {index} must be an object"
            )

        from_account = value.get("from_account")
        to_accounts = value.get("to_accounts")
        cc_accounts = value.get("cc_accounts", [])
        subject = value.get("subject")
        body = value.get("body")

        if from_account not in accounts:
            raise ValueError(
                f"unknown from_account: {from_account}"
            )

        if (
            not isinstance(to_accounts, list)
            or not to_accounts
            or not all(
                isinstance(item, str)
                and item in accounts
                for item in to_accounts
            )
        ):
            raise ValueError(
                f"invalid to_accounts at message {index}"
            )

        if (
            not isinstance(cc_accounts, list)
            or not all(
                isinstance(item, str)
                and item in accounts
                for item in cc_accounts
            )
        ):
            raise ValueError(
                f"invalid cc_accounts at message {index}"
            )

        if (
            not isinstance(subject, str)
            or not isinstance(body, str)
        ):
            raise ValueError(
                f"invalid message content at index {index}"
            )

        normalized = dict(value)

        if not isinstance(
            normalized.get("message_id"),
            str,
        ):
            digest = hashlib.sha256(
                (
                    from_account
                    + "\0"
                    + ",".join(to_accounts)
                    + "\0"
                    + subject
                    + "\0"
                    + body
                ).encode("utf-8")
            ).hexdigest()[:24]

            normalized["message_id"] = (
                f"<seed-{digest}@fanno.local>"
            )

        messages.append(normalized)

    return EmailSeed(
        accounts=accounts,
        messages=tuple(messages),
    )


class GreenMailClient:
    def __init__(
        self,
        *,
        host: str,
        imap_port: int,
        smtp_port: int,
        account: EmailAccount,
        timeout_seconds: int = 15,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        self.host = host
        self.imap_port = imap_port
        self.smtp_port = smtp_port
        self.account = account
        self.timeout_seconds = timeout_seconds

    def ensure_account(self) -> None:
        connection = self._connect_imap()
        try:
            connection.select("INBOX")
        finally:
            self._close_imap(connection)

    def search(
        self,
        *,
        query: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        connection = self._connect_imap()

        try:
            status, _ = connection.select(
                "INBOX",
                readonly=True,
            )
            if status != "OK":
                raise EmailBackendFailure(
                    "mailbox_unavailable"
                )

            status, data = connection.uid(
                "search",
                None,
                "ALL",
            )

            if status != "OK":
                raise EmailBackendFailure(
                    "email_search_failed"
                )

            raw_uids = (
                data[0].split()
                if data and data[0]
                else []
            )

            results: list[dict[str, Any]] = []

            for raw_uid in reversed(raw_uids):
                message = self._fetch_message(
                    connection,
                    raw_uid.decode("ascii"),
                )

                sender_value = message.get("from", "")
                subject_value = message.get(
                    "subject",
                    "",
                )
                body_value = message.get("body", "")

                if (
                    sender
                    and sender.lower()
                    not in sender_value.lower()
                ):
                    continue

                if (
                    subject
                    and subject.lower()
                    not in subject_value.lower()
                ):
                    continue

                if query:
                    haystack = "\n".join(
                        [
                            sender_value,
                            subject_value,
                            body_value,
                        ]
                    ).lower()

                    if query.lower() not in haystack:
                        continue

                results.append(
                    {
                        "message_id": message[
                            "message_id"
                        ],
                        "uid": message["uid"],
                        "from": sender_value,
                        "to": message["to"],
                        "cc": message["cc"],
                        "subject": subject_value,
                        "date": message["date"],
                        "has_attachments": bool(
                            message["attachments"]
                        ),
                    }
                )

                if len(results) >= max_results:
                    break

            return results
        finally:
            self._close_imap(connection)

    def read(
        self,
        message_id: str,
    ) -> dict[str, Any]:
        connection = self._connect_imap()

        try:
            status, _ = connection.select(
                "INBOX",
                readonly=True,
            )
            if status != "OK":
                raise EmailBackendFailure(
                    "mailbox_unavailable"
                )

            return self._find_message(
                connection,
                message_id,
            )
        finally:
            self._close_imap(connection)

    def get_attachment(
        self,
        *,
        message_id: str,
        attachment_id: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Fetch one decoded MIME attachment from INBOX."""
        if max_bytes <= 0:
            raise ValueError(
                "max_bytes must be positive"
            )

        connection = self._connect_imap()

        try:
            status, _ = connection.select(
                "INBOX",
                readonly=True,
            )
            if status != "OK":
                raise EmailBackendFailure(
                    "mailbox_unavailable"
                )

            message = self._find_message(
                connection,
                message_id,
                include_attachment_content=True,
            )

            for attachment in message[
                "attachments"
            ]:
                if (
                    attachment["attachment_id"]
                    != attachment_id
                ):
                    continue

                content = attachment.get(
                    "content"
                )
                if not isinstance(
                    content,
                    bytes,
                ):
                    raise EmailBackendFailure(
                        "email_read_failed",
                        attachment_id,
                    )
                if len(content) > max_bytes:
                    raise EmailBackendFailure(
                        "attachment_too_large",
                        attachment_id,
                    )

                return {
                    "attachment_id": (
                        attachment_id
                    ),
                    "filename": attachment[
                        "filename"
                    ],
                    "content_type": attachment[
                        "content_type"
                    ],
                    "size": len(content),
                    "sha256": hashlib.sha256(
                        content
                    ).hexdigest(),
                    "content": content,
                }

            raise EmailBackendFailure(
                "attachment_not_found",
                attachment_id,
            )
        finally:
            self._close_imap(connection)

    def send(
        self,
        *,
        recipients: list[str],
        cc: list[str],
        subject: str,
        body: str,
        message_id: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        message = EmailMessage()
        message["From"] = (
            f"{self.account.display_name} "
            f"<{self.account.address}>"
        )
        message["To"] = ", ".join(recipients)

        if cc:
            message["Cc"] = ", ".join(cc)

        message["Subject"] = subject

        if message_id:
            message["Message-ID"] = message_id

        if date:
            message["Date"] = date

        message.set_content(body)

        try:
            with smtplib.SMTP(
                self.host,
                self.smtp_port,
                timeout=self.timeout_seconds,
            ) as connection:
                connection.ehlo_or_helo_if_needed()
                connection.send_message(
                    message,
                    from_addr=self.account.address,
                    to_addrs=[*recipients, *cc],
                )
        except TimeoutError as exc:
            raise EmailBackendFailure(
                "email_timeout",
                str(exc),
                timed_out=True,
                retryable=True,
            ) from exc
        except (
            OSError,
            smtplib.SMTPException,
        ) as exc:
            raise EmailBackendFailure(
                "email_send_failed",
                str(exc),
                retryable=True,
            ) from exc

        return {
            "from": self.account.address,
            "to": recipients,
            "cc": cc,
            "subject": subject,
            "accepted": True,
            "message_id": message.get(
                "Message-ID"
            ),
        }

    def snapshot(self) -> list[dict[str, Any]]:
        return self.search(max_results=10000)

    def _connect_imap(self) -> imaplib.IMAP4:
        try:
            connection = imaplib.IMAP4(
                self.host,
                self.imap_port,
                timeout=self.timeout_seconds,
            )
            connection.login(
                self.account.address,
                self.account.password,
            )
            return connection
        except TimeoutError as exc:
            raise EmailBackendFailure(
                "email_timeout",
                str(exc),
                timed_out=True,
                retryable=True,
            ) from exc
        except (
            OSError,
            imaplib.IMAP4.error,
        ) as exc:
            raise EmailBackendFailure(
                "email_connection_failed",
                str(exc),
                retryable=True,
            ) from exc

    @staticmethod
    def _close_imap(
        connection: imaplib.IMAP4,
    ) -> None:
        try:
            connection.close()
        except Exception:
            pass

        try:
            connection.logout()
        except Exception:
            pass

    @staticmethod
    def _fetch_message(
        connection: imaplib.IMAP4,
        uid: str,
        *,
        include_attachment_content: bool = False,
    ) -> dict[str, Any]:
        status, data = connection.uid(
            "fetch",
            uid,
            "(RFC822)",
        )

        if status != "OK":
            raise EmailBackendFailure(
                "email_read_failed",
                uid,
            )

        raw_message: bytes | None = None

        for item in data or []:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], bytes)
            ):
                raw_message = item[1]
                break

        if raw_message is None:
            raise EmailBackendFailure(
                "email_read_failed",
                uid,
            )

        parsed = BytesParser(
            policy=default
        ).parsebytes(raw_message)

        body = ""
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
                    attachment = {
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
                        "sha256": hashlib.sha256(
                            payload
                        ).hexdigest(),
                    }

                    if include_attachment_content:
                        attachment["content"] = (
                            payload
                        )

                    attachments.append(
                        attachment
                    )
                    continue

                if (
                    part.get_content_type()
                    == "text/plain"
                    and not body
                ):
                    charset = (
                        part.get_content_charset()
                        or "utf-8"
                    )
                    body = payload.decode(
                        charset,
                        errors="replace",
                    )
        else:
            payload = parsed.get_payload(
                decode=True
            ) or b""

            charset = (
                parsed.get_content_charset()
                or "utf-8"
            )

            body = payload.decode(
                charset,
                errors="replace",
            )

        header_message_id = parsed.get(
            "Message-ID"
        )

        return {
            "uid": uid,
            "message_id": (
                str(header_message_id)
                if header_message_id
                else uid
            ),
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
            "body": body,
            "attachments": attachments,
        }

    @classmethod
    def _find_message(
        cls,
        connection: imaplib.IMAP4,
        message_id: str,
        *,
        include_attachment_content: bool = False,
    ) -> dict[str, Any]:
        status, data = connection.uid(
            "search",
            None,
            "ALL",
        )

        if status != "OK":
            raise EmailBackendFailure(
                "email_search_failed"
            )

        raw_uids = (
            data[0].split()
            if data and data[0]
            else []
        )

        for raw_uid in raw_uids:
            message = cls._fetch_message(
                connection,
                raw_uid.decode("ascii"),
                include_attachment_content=(
                    include_attachment_content
                ),
            )

            if (
                message["message_id"]
                == message_id
                or message["uid"]
                == message_id
            ):
                return message

        raise EmailBackendFailure(
            "message_not_found",
            message_id,
        )


def seed_greenmail(
    *,
    seed: EmailSeed,
    host: str,
    imap_port: int,
    smtp_port: int,
    timeout_seconds: int,
) -> None:
    for account in seed.accounts.values():
        client = GreenMailClient(
            host=host,
            imap_port=imap_port,
            smtp_port=smtp_port,
            account=account,
            timeout_seconds=timeout_seconds,
        )

        last_error: EmailBackendFailure | None = None

        for attempt in range(10):
            try:
                client.ensure_account()
                last_error = None
                break
            except EmailBackendFailure as exc:
                last_error = exc

                if not exc.info.retryable:
                    raise

                time.sleep(
                    min(
                    0.25 * (attempt + 1),
                    1.0,
                )
            )

        if last_error is not None:
            raise last_error

    for message in seed.messages:
        from_account = seed.accounts[
            message["from_account"]
        ]

        recipients = [
            seed.accounts[item].address
            for item in message["to_accounts"]
        ]

        cc = [
            seed.accounts[item].address
            for item in message.get(
                "cc_accounts",
                [],
            )
        ]

        GreenMailClient(
            host=host,
            imap_port=imap_port,
            smtp_port=smtp_port,
            account=from_account,
            timeout_seconds=timeout_seconds,
        ).send(
            recipients=recipients,
            cc=cc,
            subject=message["subject"],
            body=message["body"],
            message_id=message.get(
                "message_id"
            ),
            date=message.get("date"),
        )


def _decode_header(value: str) -> str:
    result: list[str] = []

    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            result.append(
                part.decode(
                    charset or "utf-8",
                    errors="replace",
                )
            )
        else:
            result.append(part)

    return "".join(result)
