from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fanno_legal.state.database import (
    StateDatabase,
    canonical_json,
)


EMPTY_MAILBOX_SNAPSHOT = {
    "schema_version": 1,
    "accounts": {},
}

EMPTY_CALENDAR_SNAPSHOT = {
    "schema_version": 1,
    "calendars": {},
}


def build_business_snapshot(
    database: StateDatabase,
) -> dict:
    """Return database state and its stable digest."""
    state = database.business_snapshot()

    digest = hashlib.sha256(
        canonical_json(
            state
        ).encode("utf-8")
    ).hexdigest()

    return {
        **state,
        "digest": digest,
    }


def build_environment_snapshot(
    database: StateDatabase,
    workspace: Path,
    *,
    mailboxes: dict[
        str,
        Any,
    ] | None = None,
    calendars: dict[
        str,
        Any,
    ] | None = None,
) -> dict:
    """
    Return database, workspace and trusted mailbox state.

    Mail bodies are represented by size and SHA-256 only.
    """
    business_state = (
        database.business_snapshot()
    )
    workspace = Path(
        workspace
    ).resolve()

    files: dict[
        str,
        dict[str, object],
    ] = {}

    if workspace.exists():
        for path in sorted(
            workspace.rglob("*")
        ):
            if path.is_symlink():
                continue
            if not path.is_file():
                continue

            content = path.read_bytes()
            relative = path.relative_to(
                workspace
            ).as_posix()

            files[relative] = {
                "size": len(content),
                "sha256": hashlib.sha256(
                    content
                ).hexdigest(),
            }

    mailbox_state = (
        mailboxes
        if mailboxes is not None
        else EMPTY_MAILBOX_SNAPSHOT
    )
    calendar_state = (
        calendars
        if calendars is not None
        else EMPTY_CALENDAR_SNAPSHOT
    )

    digest_payload = {
        "business_state": business_state,
        "files": files,
        "mailboxes": mailbox_state,
        "calendars": calendar_state,
    }

    digest = hashlib.sha256(
        canonical_json(
            digest_payload
        ).encode("utf-8")
    ).hexdigest()

    return {
        **business_state,
        "files": files,
        "mailboxes": mailbox_state,
        "calendars": calendar_state,
        "digest": digest,
    }
