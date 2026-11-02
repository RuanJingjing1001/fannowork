from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from typing import Any

from fanno_work.backends import BackendFailure
from fanno_work.backends import (
    execution_evidence,
)

from fanno_legal.backends.email.greenmail import (
    EmailAccount,
    GreenMailClient,
)


WORKSPACE_ROOT = Path("/workspace").resolve()


def required_string(
    request: dict[str, Any],
    key: str,
) -> str:
    value = request.get(key)

    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{key} must be a non-empty string"
        )

    return value


def string_list(
    request: dict[str, Any],
    key: str,
) -> list[str]:
    value = request.get(key, [])

    if (
        not isinstance(value, list)
        or not all(
            isinstance(item, str) and item
            for item in value
        )
    ):
        raise ValueError(
            f"{key} must be a string list"
        )

    return value


def resolve_attachment_target(
    request: dict[str, Any],
) -> tuple[str, Path]:
    value = request.get("target_path")
    prefixes = request.get(
        "attachment_writable_prefixes"
    )

    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
    ):
        raise ValueError(
            "target_path must be relative"
        )
    if (
        not isinstance(prefixes, list)
        or not prefixes
        or not all(
            isinstance(item, str) and item
            for item in prefixes
        )
    ):
        raise ValueError(
            "attachment_writable_prefixes "
            "must be a non-empty string list"
        )

    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or pure.as_posix() == "."
    ):
        raise ValueError(
            "target_path must be relative"
        )

    relative = pure.as_posix()
    normalized_prefixes = tuple(
        PurePosixPath(prefix)
        .as_posix()
        .rstrip("/")
        + "/"
        for prefix in prefixes
    )

    if not any(
        relative.startswith(prefix)
        for prefix in normalized_prefixes
    ):
        raise ValueError(
            "target_path is not permitted"
        )

    current = WORKSPACE_ROOT
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(
                "target_path contains a symlink"
            )

    target = WORKSPACE_ROOT.joinpath(
        *pure.parts
    ).resolve()
    if not target.is_relative_to(
        WORKSPACE_ROOT
    ):
        raise ValueError(
            "target_path escapes workspace"
        )

    return relative, target


def write_attachment(
    target: Path,
    content: bytes,
) -> tuple[bool, bool]:
    existed = target.exists()
    if existed:
        if not target.is_file():
            raise ValueError(
                "target_path is not a file"
            )
        if target.read_bytes() == content:
            return False, False

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
            temporary_path = Path(handle.name)

        os.replace(temporary_path, target)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )
        raise

    return not existed, True


def execute(
    request: dict[str, Any],
) -> dict[str, Any]:
    timeout_seconds = request.get(
        "timeout_seconds"
    )

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ValueError(
            "timeout_seconds must be positive"
        )

    account = EmailAccount(
        id=required_string(
            request,
            "account_id",
        ),
        address=required_string(
            request,
            "account_address",
        ),
        password=str(
            request.get(
                "account_password",
                "",
            )
        ),
        display_name=required_string(
            request,
            "account_display_name",
        ),
    )

    client = GreenMailClient(
        host=required_string(
            request,
            "host",
        ),
        imap_port=int(
            request.get("imap_port")
        ),
        smtp_port=int(
            request.get("smtp_port")
        ),
        account=account,
        timeout_seconds=timeout_seconds,
    )

    operation = required_string(
        request,
        "operation",
    )

    if operation == "search":
        output = {
            "messages": client.search(
                query=request.get("query"),
                sender=request.get("sender"),
                subject=request.get("subject"),
                max_results=int(
                    request.get(
                        "max_results",
                        100,
                    )
                ),
            )
        }
        state_changed = False

    elif operation == "read":
        output = client.read(
            required_string(
                request,
                "message_id",
            )
        )
        state_changed = False

    elif operation == "send":
        output = client.send(
            recipients=string_list(
                request,
                "to",
            ),
            cc=string_list(
                request,
                "cc",
            ),
            subject=required_string(
                request,
                "subject",
            ),
            body=required_string(
                request,
                "body",
            ),
        )
        state_changed = True

    elif operation == "download_attachment":
        max_bytes = request.get(
            "max_attachment_bytes"
        )
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes <= 0
        ):
            raise ValueError(
                "max_attachment_bytes must be positive"
            )

        relative, target = (
            resolve_attachment_target(
                request
            )
        )
        attachment = client.get_attachment(
            message_id=required_string(
                request,
                "message_id",
            ),
            attachment_id=required_string(
                request,
                "attachment_id",
            ),
            max_bytes=max_bytes,
        )
        content = attachment.pop("content")
        if not isinstance(content, bytes):
            raise ValueError(
                "attachment content is invalid"
            )

        try:
            created, changed = write_attachment(
                target,
                content,
            )
        except OSError as exc:
            raise BackendFailure(
                "write_failed",
                str(exc),
                backend="greenmail",
            ) from exc
        output = {
            **attachment,
            "path": relative,
            "created": created,
            "changed": changed,
            "size": len(content),
            "sha256": hashlib.sha256(
                content
            ).hexdigest(),
        }
        state_changed = changed

    else:
        raise ValueError(
            f"unsupported operation: {operation}"
        )

    return {
        "ok": True,
        "operation": operation,
        "output": output,
        "state_changed": state_changed,
        "execution_evidence": execution_evidence(
            backend="greenmail",
            execution_scope="task_container",
            timeout_seconds=timeout_seconds,
            runtime="docker",
        ),
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)

        if not isinstance(request, dict):
            raise ValueError(
                "request must be an object"
            )

        response = execute(request)
        status = 0

    except BackendFailure as exc:
        response = {
            "ok": False,
            "error": exc.code,
            "backend_error": exc.as_dict(),
        }
        status = 1

    except (
        TypeError,
        ValueError,
    ) as exc:
        response = {
            "ok": False,
            "error": "invalid_email_request",
            "backend_error": {
                "code": "invalid_email_request",
                "detail": str(exc),
                "backend": "greenmail",
                "timed_out": False,
                "retryable": False,
            },
        }
        status = 1

    json.dump(
        response,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )

    return status


if __name__ == "__main__":
    raise SystemExit(main())
