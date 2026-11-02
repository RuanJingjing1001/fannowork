from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from fanno_work.models import (
    ActionRequest,
    ActionResult,
)

from fanno_legal.backends.email import (
    EmailBackend,
    EmailBackendFailure,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
    FilesystemFailure,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)


class EmailFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EmailCapability:
    ACTION_NAMES = frozenset(
        {
            "email.search",
            "email.read",
            "email.send",
            "email.download_attachment",
        }
    )

    def __init__(
        self,
        *,
        backend: EmailBackend,
        filesystem: FilesystemCapability,
        database: StateDatabase,
        audit: AuditLog,
        config: dict[str, Any],
    ) -> None:
        self.backend = backend
        self.filesystem = filesystem
        self.database = database
        self.audit = audit

        self.max_results = self._positive_integer(
            config,
            "max_results",
        )
        self.max_query_characters = (
            self._positive_integer(
                config,
                "max_query_characters",
            )
        )
        self.max_subject_characters = (
            self._positive_integer(
                config,
                "max_subject_characters",
            )
        )
        self.max_body_characters = (
            self._positive_integer(
                config,
                "max_body_characters",
            )
        )
        self.max_attachment_bytes = (
            self._positive_integer(
                config,
                "max_attachment_bytes",
            )
        )

        attachment_prefixes = config.get(
            "attachment_writable_prefixes"
        )
        if (
            not isinstance(
                attachment_prefixes,
                list,
            )
            or not attachment_prefixes
            or not all(
                isinstance(item, str)
                and item
                for item in attachment_prefixes
            )
        ):
            raise ValueError(
                "email.attachment_writable_prefixes "
                "must be a non-empty string list"
            )

        self.attachment_writable_prefixes = (
            tuple(
                self._normalize_prefix(item)
                for item in attachment_prefixes
            )
        )

        domains = config.get(
            "allowed_recipient_domains",
            [],
        )

        if (
            not isinstance(domains, list)
            or not all(
                isinstance(item, str)
                and item
                for item in domains
            )
        ):
            raise ValueError(
                "email.allowed_recipient_domains "
                "must be a string list"
            )

        self.allowed_recipient_domains = (
            frozenset(
                item.lower()
                for item in domains
            )
        )

    def act(
        self,
        actor: str,
        request: ActionRequest,
    ) -> ActionResult:
        version_before = (
            self.database.get_state_version()
        )

        try:
            result = self._dispatch(request)
        except (
            EmailFailure,
            EmailBackendFailure,
        ) as exc:
            result = ActionResult(
                ok=False,
                error=exc.code,
            )

        version_after = (
            self.database.get_state_version()
        )

        self.audit.append(
            actor=actor,
            action=request.name,
            arguments=self._audit_arguments(
                request
            ),
            ok=result.ok,
            error=result.error,
            state_version_before=version_before,
            state_version_after=version_after,
        )

        return result

    def _dispatch(
        self,
        request: ActionRequest,
    ) -> ActionResult:
        if request.name == "email.search":
            return self._search(
                request.arguments
            )

        if request.name == "email.read":
            return self._read(
                request.arguments
            )

        if request.name == "email.send":
            return self._send(
                request.arguments
            )

        if (
            request.name
            == "email.download_attachment"
        ):
            return self._download_attachment(
                request.arguments
            )

        return ActionResult(
            ok=False,
            error="unknown_action",
        )

    def _search(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        query = arguments.get("query")
        sender = arguments.get("sender")
        subject = arguments.get("subject")
        max_results = arguments.get(
            "max_results",
            self.max_results,
        )

        for value in (
            query,
            sender,
            subject,
        ):
            if (
                value is not None
                and not isinstance(value, str)
            ):
                raise EmailFailure(
                    "invalid_query"
                )

        if (
            isinstance(query, str)
            and len(query)
            > self.max_query_characters
        ):
            raise EmailFailure(
                "query_too_large"
            )

        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or max_results <= 0
        ):
            raise EmailFailure(
                "invalid_result_limit"
            )

        result = self.backend.search(
            query=query,
            sender=sender,
            subject=subject,
            max_results=min(
                max_results,
                self.max_results,
            ),
        )

        return ActionResult(
            ok=True,
            output=result,
            state_changed=False,
        )

    def _read(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        message_id = arguments.get(
            "message_id"
        )

        if (
            not isinstance(message_id, str)
            or not message_id
        ):
            raise EmailFailure(
                "invalid_message_id"
            )

        result = self.backend.read(
            message_id=message_id
        )

        return ActionResult(
            ok=True,
            output=result,
            state_changed=False,
        )

    def _send(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        to = self._address_list(
            arguments.get("to"),
            required=True,
        )
        cc = self._address_list(
            arguments.get("cc", []),
            required=False,
        )

        subject = arguments.get("subject")
        body = arguments.get("body")

        if (
            not isinstance(subject, str)
            or not subject
        ):
            raise EmailFailure(
                "invalid_subject"
            )

        if (
            not isinstance(body, str)
            or not body
        ):
            raise EmailFailure(
                "invalid_body"
            )

        if (
            len(subject)
            > self.max_subject_characters
        ):
            raise EmailFailure(
                "subject_too_large"
            )

        if (
            len(body)
            > self.max_body_characters
        ):
            raise EmailFailure(
                "body_too_large"
            )

        for address in [*to, *cc]:
            self._check_recipient(address)

        result = self.backend.send(
            to=to,
            cc=cc,
            subject=subject,
            body=body,
        )

        self.database.advance_state_version()

        return ActionResult(
            ok=True,
            output=result,
            state_changed=True,
        )

    def _download_attachment(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        message_id = arguments.get(
            "message_id"
        )
        attachment_id = arguments.get(
            "attachment_id"
        )
        target_path = arguments.get(
            "target_path"
        )

        if (
            not isinstance(message_id, str)
            or not message_id
        ):
            raise EmailFailure(
                "invalid_message_id"
            )
        if (
            not isinstance(attachment_id, str)
            or not attachment_id
        ):
            raise EmailFailure(
                "invalid_attachment_id"
            )
        if not isinstance(target_path, str):
            raise EmailFailure(
                "invalid_path"
            )

        try:
            relative, path = (
                self.filesystem
                .resolve_writable_path(
                    target_path
                )
            )
        except FilesystemFailure as exc:
            raise EmailFailure(
                exc.code
            ) from exc

        if not self._is_allowed(
            relative,
            self.attachment_writable_prefixes,
        ):
            raise EmailFailure(
                "permission_denied"
            )

        result = self.backend.download_attachment(
            message_id=message_id,
            attachment_id=attachment_id,
            target_path=relative,
            max_bytes=self.max_attachment_bytes,
        )

        if not path.is_file():
            raise EmailBackendFailure(
                "invalid_backend_response",
                "attachment file was not created",
            )

        content = path.read_bytes()
        expected_size = result.get("size")
        expected_sha256 = result.get("sha256")
        actual_sha256 = hashlib.sha256(
            content
        ).hexdigest()

        if (
            expected_size != len(content)
            or expected_sha256 != actual_sha256
        ):
            raise EmailBackendFailure(
                "invalid_backend_response",
                "attachment evidence mismatch",
            )

        changed = result.get("changed")
        if not isinstance(changed, bool):
            raise EmailBackendFailure(
                "invalid_backend_response",
                "changed must be boolean",
            )

        if changed:
            self.database.advance_state_version()

        return ActionResult(
            ok=True,
            output=result,
            state_changed=changed,
        )

    def _check_recipient(
        self,
        address: str,
    ) -> None:
        if "@" not in address:
            raise EmailFailure(
                "invalid_recipient"
            )

        domain = address.rsplit(
            "@",
            1,
        )[1].lower()

        if (
            self.allowed_recipient_domains
            and domain
            not in self.allowed_recipient_domains
        ):
            raise EmailFailure(
                "recipient_not_allowed"
            )

    @staticmethod
    def _address_list(
        value: Any,
        *,
        required: bool,
    ) -> list[str]:
        if (
            not isinstance(value, list)
            or (
                required
                and not value
            )
            or not all(
                isinstance(item, str)
                and item
                for item in value
            )
        ):
            raise EmailFailure(
                "invalid_recipient"
            )

        return list(value)

    @staticmethod
    def _audit_arguments(
        request: ActionRequest,
    ) -> dict[str, Any]:
        arguments = dict(
            request.arguments
        )

        body = arguments.pop("body", None)

        if isinstance(body, str):
            arguments["body_character_count"] = (
                len(body)
            )
            arguments["body_sha256"] = (
                hashlib.sha256(
                    body.encode("utf-8")
                ).hexdigest()
            )

        return arguments

    @staticmethod
    def _positive_integer(
        config: dict[str, Any],
        key: str,
    ) -> int:
        value = config.get(key)

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValueError(
                f"email.{key} must be "
                "a positive integer"
            )

        return value

    @staticmethod
    def _normalize_prefix(value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() == "."
        ):
            raise ValueError(
                "email attachment prefix is invalid"
            )

        return path.as_posix().rstrip("/") + "/"

    @staticmethod
    def _is_allowed(
        path: str,
        prefixes: tuple[str, ...],
    ) -> bool:
        return any(
            path == prefix.rstrip("/")
            or path.startswith(prefix)
            for prefix in prefixes
        )
