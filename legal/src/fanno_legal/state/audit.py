from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditLog:
    """Evaluator-visible append-only action log."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def append(
        self,
        *,
        actor: str,
        action: str,
        arguments: dict[str, Any],
        ok: bool,
        error: str | None,
        state_version_before: int,
        state_version_after: int,
    ) -> dict[str, Any]:
        events = self.read_all()
        sequence = (
            events[-1]["sequence"] + 1
            if events
            else 1
        )

        event = {
            "sequence": sequence,
            "event_id": f"audit-{sequence:06d}",
            "actor": actor,
            "action": action,
            "arguments": arguments,
            "ok": ok,
            "error": error,
            "state_version_before": state_version_before,
            "state_version_after": state_version_after,
            "state_changed": (
                state_version_before
                != state_version_after
            ),
        }

        encoded = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        with self.path.open(
            "a",
            encoding="utf-8",
        ) as stream:
            stream.write(encoded)
            stream.write("\n")
            stream.flush()

        return event

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        events = []

        for line in self.path.read_text(
            encoding="utf-8"
        ).splitlines():
            if line.strip():
                events.append(json.loads(line))

        return events
