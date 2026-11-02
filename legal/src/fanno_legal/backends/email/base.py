from __future__ import annotations

from typing import Any, Protocol


class EmailBackend(Protocol):
    """Backend contract used by the agent-facing email capability."""

    backend_id: str

    def search(
        self,
        *,
        query: str | None,
        sender: str | None,
        subject: str | None,
        max_results: int,
    ) -> dict[str, Any]:
        ...

    def read(
        self,
        *,
        message_id: str,
    ) -> dict[str, Any]:
        ...

    def send(
        self,
        *,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        ...

    def download_attachment(
        self,
        *,
        message_id: str,
        attachment_id: str,
        target_path: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        ...
