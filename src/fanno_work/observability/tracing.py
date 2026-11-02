from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Iterator
import uuid


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}

_LARGE_CONTENT_KEYS = {
    "body",
    "content",
    "document_text",
    "text",
}

_CURRENT_BINDING: ContextVar[
    TraceBinding | None
] = ContextVar(
    "fanno_trace_binding",
    default=None,
)


def _timestamp() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _hash_text(value: str) -> dict[str, Any]:
    encoded = value.encode("utf-8")

    return {
        "redacted": True,
        "character_count": len(value),
        "byte_count": len(encoded),
        "sha256": hashlib.sha256(
            encoded
        ).hexdigest(),
    }


def _safe_value(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
) -> Any:
    if depth > 6:
        return "<maximum-depth>"

    normalized_key = (
        key.casefold()
        if isinstance(key, str)
        else None
    )

    if normalized_key in _SENSITIVE_KEYS:
        return "<redacted>"

    if (
        normalized_key in _LARGE_CONTENT_KEYS
        and isinstance(value, str)
    ):
        return _hash_text(value)

    if value is None:
        return None

    if isinstance(
        value,
        (bool, int, float),
    ):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, str):
        if len(value) <= 1000:
            return value

        return {
            "truncated": True,
            "prefix": value[:500],
            **_hash_text(value),
        }

    if isinstance(value, dict):
        return {
            str(item_key): _safe_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value
            in value.items()
        }

    if isinstance(value, (list, tuple)):
        maximum = 50
        result = [
            _safe_value(
                item,
                depth=depth + 1,
            )
            for item in value[:maximum]
        ]

        if len(value) > maximum:
            result.append(
                {
                    "truncated_items": (
                        len(value) - maximum
                    )
                }
            )

        return result

    return repr(value)


def summarize_request(
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    summary = _safe_value(arguments)

    if not isinstance(summary, dict):
        summary = {
            "arguments": summary,
        }

    summary["action"] = action
    return summary


def summarize_action_result(
    result: Any,
) -> dict[str, Any]:
    output = getattr(
        result,
        "output",
        {},
    )

    if not isinstance(output, dict):
        output = {}

    summary: dict[str, Any] = {
        "ok": bool(
            getattr(result, "ok", False)
        ),
        "error": getattr(
            result,
            "error",
            None,
        ),
        "state_changed": bool(
            getattr(
                result,
                "state_changed",
                False,
            )
        ),
        "output_keys": sorted(output),
    }

    scalar_keys = (
        "path",
        "backend",
        "backend_version",
        "sheet",
        "range",
        "rows",
        "columns",
        "cell_count",
        "sheet_count",
        "scan_cell_count",
        "total_matches",
        "returned_matches",
        "calendar_count",
        "event_count",
        "character_count",
        "page_count",
        "size",
        "sha256",
        "source_sha256",
        "created",
        "truncated",
    )

    for key in scalar_keys:
        if key in output:
            summary[key] = _safe_value(
                output[key],
                key=key,
            )

    sheets = output.get("sheets")
    if isinstance(sheets, list):
        summary["sheet_names"] = [
            item.get("name")
            for item in sheets
            if isinstance(item, dict)
            and isinstance(
                item.get("name"),
                str,
            )
        ]

    entries = output.get("entries")
    if isinstance(entries, list):
        summary["entry_count"] = len(
            entries
        )
        summary["entry_paths"] = [
            item.get("path")
            for item in entries[:50]
            if isinstance(item, dict)
        ]

    matches = output.get("matches")
    if isinstance(matches, list):
        summary["match_locations"] = [
            {
                "sheet": item.get("sheet"),
                "address": item.get(
                    "address"
                ),
            }
            for item in matches[:50]
            if isinstance(item, dict)
        ]

    execution_evidence = output.get(
        "execution_evidence"
    )
    if isinstance(execution_evidence, dict):
        summary["execution_evidence"] = (
            _safe_value(execution_evidence)
        )

    return summary


def _record_index(
    snapshot: dict[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}

    collections = snapshot.get(
        "collections",
        [],
    )

    if not isinstance(collections, list):
        return result

    for collection in collections:
        if not isinstance(collection, dict):
            continue

        collection_id = collection.get("id")
        records = collection.get(
            "records",
            [],
        )

        if not isinstance(
            collection_id,
            str,
        ):
            continue
        if not isinstance(records, list):
            continue

        for record in records:
            if not isinstance(record, dict):
                continue

            record_id = record.get("id")
            if not isinstance(
                record_id,
                str,
            ):
                continue

            encoded = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")

            result[
                f"{collection_id}/{record_id}"
            ] = hashlib.sha256(
                encoded
            ).hexdigest()

    return result

def _mailbox_index(
    snapshot: dict[str, Any],
) -> dict[
    str,
    dict[str, Any],
]:
    result: dict[
        str,
        dict[str, Any],
    ] = {}

    mailboxes = snapshot.get(
        "mailboxes",
        {},
    )

    if not isinstance(
        mailboxes,
        dict,
    ):
        return result

    accounts = mailboxes.get(
        "accounts",
        {},
    )

    if not isinstance(
        accounts,
        dict,
    ):
        return result

    for account_id, account in (
        accounts.items()
    ):
        if not isinstance(
            account_id,
            str,
        ):
            continue
        if not isinstance(
            account,
            dict,
        ):
            continue

        folders = account.get(
            "folders",
            {},
        )

        if not isinstance(
            folders,
            dict,
        ):
            continue

        for folder_name, folder in (
            folders.items()
        ):
            if not isinstance(
                folder_name,
                str,
            ):
                continue
            if not isinstance(
                folder,
                dict,
            ):
                continue

            messages = folder.get(
                "messages",
                [],
            )

            if not isinstance(
                messages,
                list,
            ):
                continue

            occurrence: dict[
                str,
                int,
            ] = {}

            for message in messages:
                if not isinstance(
                    message,
                    dict,
                ):
                    continue

                message_id = message.get(
                    "message_id"
                )

                if not isinstance(
                    message_id,
                    str,
                ):
                    continue

                number = occurrence.get(
                    message_id,
                    0,
                )
                occurrence[message_id] = (
                    number + 1
                )

                key = (
                    f"{account_id}/"
                    f"{folder_name}/"
                    f"{message_id}#{number}"
                )

                result[key] = message

    return result


def snapshot_diff(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_files = before.get(
        "files",
        {},
    )
    after_files = after.get(
        "files",
        {},
    )

    if not isinstance(before_files, dict):
        before_files = {}
    if not isinstance(after_files, dict):
        after_files = {}

    before_paths = set(before_files)
    after_paths = set(after_files)

    created = sorted(
        after_paths - before_paths
    )
    deleted = sorted(
        before_paths - after_paths
    )
    modified = sorted(
        path
        for path
        in before_paths & after_paths
        if before_files[path]
        != after_files[path]
    )

    file_changes: list[
        dict[str, Any]
    ] = []

    for path in created:
        file_changes.append(
            {
                "path": path,
                "change": "created",
                "before": None,
                "after": after_files[path],
            }
        )

    for path in modified:
        file_changes.append(
            {
                "path": path,
                "change": "modified",
                "before": before_files[path],
                "after": after_files[path],
            }
        )

    for path in deleted:
        file_changes.append(
            {
                "path": path,
                "change": "deleted",
                "before": before_files[path],
                "after": None,
            }
        )

    before_records = _record_index(
        before
    )
    after_records = _record_index(
        after
    )

    record_keys = (
        set(before_records)
        | set(after_records)
    )

    changed_records = sorted(
        key
        for key in record_keys
        if before_records.get(key)
        != after_records.get(key)
    )

    before_mailboxes = _mailbox_index(
        before
    )
    after_mailboxes = _mailbox_index(
        after
    )

    mailbox_keys = sorted(
        set(before_mailboxes)
        | set(after_mailboxes)
    )

    mailbox_changes: list[
        dict[str, Any]
    ] = []

    for key in mailbox_keys:
        before_message = (
            before_mailboxes.get(key)
        )
        after_message = (
            after_mailboxes.get(key)
        )

        if before_message is None:
            mailbox_changes.append(
                {
                    "message": key,
                    "change": "created",
                    "before": None,
                    "after": after_message,
                }
            )
            continue

        if after_message is None:
            mailbox_changes.append(
                {
                    "message": key,
                    "change": "deleted",
                    "before": before_message,
                    "after": None,
                }
            )
            continue

        if before_message != after_message:
            mailbox_changes.append(
                {
                    "message": key,
                    "change": "modified",
                    "before": before_message,
                    "after": after_message,
                }
            )

    def calendar_index(
        snapshot: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        calendar_state = snapshot.get("calendars", {})
        if not isinstance(calendar_state, dict):
            return result
        calendars = calendar_state.get("calendars", {})
        if not isinstance(calendars, dict):
            return result
        for calendar_name, calendar in calendars.items():
            if not isinstance(calendar_name, str) or not isinstance(calendar, dict):
                continue
            events = calendar.get("events", {})
            if not isinstance(events, dict):
                continue
            for uid, event in events.items():
                if isinstance(uid, str) and isinstance(event, dict):
                    result[f"{calendar_name}/{uid}"] = event
        return result

    before_calendars = calendar_index(before)
    after_calendars = calendar_index(after)
    calendar_changes: list[dict[str, Any]] = []
    for key in sorted(set(before_calendars) | set(after_calendars)):
        before_event = before_calendars.get(key)
        after_event = after_calendars.get(key)
        if before_event is None:
            change = "created"
        elif after_event is None:
            change = "deleted"
        elif before_event != after_event:
            change = "modified"
        else:
            continue
        calendar_changes.append(
            {
                "event": key,
                "change": change,
                "before": before_event,
                "after": after_event,
            }
        )

    return {
        "state_version_before": (
            before.get("state_version")
        ),
        "state_version_after": (
            after.get("state_version")
        ),
        "digest_before": before.get(
            "digest"
        ),
        "digest_after": after.get(
            "digest"
        ),
        "state_changed": (
            before.get("digest")
            != after.get("digest")
        ),
        "file_changes": file_changes,
        "changed_records": (
            changed_records
        ),
        "mailbox_changes": mailbox_changes,
        "calendar_changes": calendar_changes,
    }


@dataclass(frozen=True)
class TraceBinding:
    session: TraceSession
    trace_id: str
    action_id: str


@dataclass
class TraceSpan:
    output_summary: dict[
        str,
        Any,
    ] = field(default_factory=dict)
    evidence: dict[
        str,
        Any,
    ] = field(default_factory=dict)

    def set_output_summary(
        self,
        **values: Any,
    ) -> None:
        self.output_summary.update(
            values
        )

    def set_evidence(
        self,
        **values: Any,
    ) -> None:
        self.evidence.update(values)


class TraceSession:
    def __init__(
        self,
        run_dir: Path,
        *,
        run_id: str | None = None,
        console: bool = False,
    ) -> None:
        self.run_dir = Path(
            run_dir
        ).resolve()
        self.run_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.run_id = (
            run_id
            or self.run_dir.name
        )
        self.console = console
        self.events_path = (
            self.run_dir / "events.jsonl"
        )

        self._sequence = 0
        self._lock = threading.Lock()
        self.last_binding: (
            TraceBinding | None
        ) = None

    def emit(
        self,
        event_type: str,
        *,
        layer: str,
        component: str,
        operation: str,
        status: str,
        details: dict[
            str,
            Any,
        ] | None = None,
        error: dict[
            str,
            Any,
        ] | None = None,
        duration_ms: float | None = None,
        binding: TraceBinding | None = None,
        parent_event_id: str | None = None,
        visibility: str = "debug_only",
    ) -> dict[str, Any]:
        active_binding = (
            binding
            or _CURRENT_BINDING.get()
        )

        with self._lock:
            self._sequence += 1
            sequence = self._sequence

            event_id = (
                f"evt-{sequence:06d}"
            )

            event = {
                "schema_version": 1,
                "sequence": sequence,
                "timestamp": _timestamp(),
                "run_id": self.run_id,
                "trace_id": (
                    active_binding.trace_id
                    if active_binding
                    else None
                ),
                "action_id": (
                    active_binding.action_id
                    if active_binding
                    else None
                ),
                "event_id": event_id,
                "parent_event_id": (
                    parent_event_id
                ),
                "event_type": event_type,
                "layer": layer,
                "component": component,
                "operation": operation,
                "status": status,
                "duration_ms": (
                    round(
                        duration_ms,
                        3,
                    )
                    if duration_ms
                    is not None
                    else None
                ),
                "details": _safe_value(
                    details or {}
                ),
                "error": _safe_value(
                    error
                ),
                "visibility": visibility,
            }

            encoded = json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

            with self.events_path.open(
                "a",
                encoding="utf-8",
            ) as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()

        if self.console:
            duration_text = (
                f" {event['duration_ms']} ms"
                if event["duration_ms"]
                is not None
                else ""
            )

            action_text = (
                f" action={event['action_id']}"
                if event["action_id"]
                else ""
            )

            print(
                (
                    f"[TRACE {sequence:04d}] "
                    f"{status.upper():8} "
                    f"{event_type} "
                    f"{component}.{operation}"
                    f"{action_text}"
                    f"{duration_text}"
                ),
                flush=True,
            )

        return event

    @contextmanager
    def action_context(
        self,
        action: str,
        arguments: dict[str, Any],
    ) -> Iterator[TraceBinding]:
        binding = TraceBinding(
            session=self,
            trace_id=(
                "trace-"
                + uuid.uuid4().hex
            ),
            action_id=(
                "act-"
                + uuid.uuid4().hex[:16]
            ),
        )

        self.last_binding = binding

        token = _CURRENT_BINDING.set(
            binding
        )

        self.emit(
            "action.request",
            layer="agent_interface",
            component="agent",
            operation=action,
            status="started",
            details={
                "request": summarize_request(
                    action,
                    arguments,
                )
            },
            binding=binding,
            visibility="agent_visible",
        )

        try:
            yield binding
        finally:
            _CURRENT_BINDING.reset(
                token
            )

    @contextmanager
    def span(
        self,
        *,
        layer: str,
        component: str,
        operation: str,
        details: dict[
            str,
            Any,
        ] | None = None,
        visibility: str = "debug_only",
    ) -> Iterator[TraceSpan]:
        binding = _CURRENT_BINDING.get()
        started = time.monotonic()

        start_event = self.emit(
            f"{layer}.start",
            layer=layer,
            component=component,
            operation=operation,
            status="started",
            details=details,
            binding=binding,
            visibility=visibility,
        )

        span = TraceSpan()

        try:
            yield span
        except Exception as exc:
            duration = (
                time.monotonic()
                - started
            ) * 1000

            self.emit(
                f"{layer}.finish",
                layer=layer,
                component=component,
                operation=operation,
                status="failed",
                details={
                    "output_summary": (
                        span.output_summary
                    ),
                    "evidence": (
                        span.evidence
                    ),
                },
                error={
                    "type": (
                        type(exc).__name__
                    ),
                    "message": str(exc),
                },
                duration_ms=duration,
                binding=binding,
                parent_event_id=(
                    start_event["event_id"]
                ),
                visibility=visibility,
            )
            raise
        else:
            duration = (
                time.monotonic()
                - started
            ) * 1000

            self.emit(
                f"{layer}.finish",
                layer=layer,
                component=component,
                operation=operation,
                status="success",
                details={
                    "output_summary": (
                        span.output_summary
                    ),
                    "evidence": (
                        span.evidence
                    ),
                },
                duration_ms=duration,
                binding=binding,
                parent_event_id=(
                    start_event["event_id"]
                ),
                visibility=visibility,
            )
