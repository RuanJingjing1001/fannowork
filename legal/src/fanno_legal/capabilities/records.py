from __future__ import annotations

from typing import Any

from fanno_work.models import ActionRequest, ActionResult

from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


class RecordsCapability:
    ACTION_NAMES = frozenset(
        {
            "records.get",
            "records.query",
            "records.update",
        }
    )

    def __init__(
        self,
        database: StateDatabase,
        audit: AuditLog,
    ) -> None:
        self.database = database
        self.audit = audit

    def act(
        self,
        actor: str,
        request: ActionRequest,
    ) -> ActionResult:
        version_before = self.database.get_state_version()

        result = self._dispatch(
            actor,
            request,
        )

        version_after = (
            self.database.get_state_version()
        )

        self.audit.append(
            actor=actor,
            action=request.name,
            arguments=request.arguments,
            ok=result.ok,
            error=result.error,
            state_version_before=version_before,
            state_version_after=version_after,
        )

        return result

    def _dispatch(
        self,
        actor: str,
        request: ActionRequest,
    ) -> ActionResult:
        if request.name == "records.get":
            return self._get(
                actor,
                request.arguments,
            )

        if request.name == "records.query":
            return self._query(
                actor,
                request.arguments,
            )

        if request.name == "records.update":
            return self._update(
                actor,
                request.arguments,
            )

        return ActionResult(
            ok=False,
            error="unknown_action",
        )

    def _get(
        self,
        actor: str,
        arguments: dict[str, Any],
    ) -> ActionResult:
        collection = arguments.get("collection")
        record_id = arguments.get("record_id")

        if not isinstance(collection, str):
            return self._invalid_arguments()
        if not isinstance(record_id, str):
            return self._invalid_arguments()

        policy = self.database.get_policy(
            actor,
            collection,
            "get",
        )
        if policy is None:
            return self._permission_denied()

        record = self.database.get_record(
            collection,
            record_id,
        )
        if record is None:
            return ActionResult(
                ok=False,
                error="record_not_found",
            )

        return ActionResult(
            ok=True,
            output={
                "record": self._project_record(
                    record,
                    policy["allowed_fields"],
                )
            },
        )

    def _query(
        self,
        actor: str,
        arguments: dict[str, Any],
    ) -> ActionResult:
        collection = arguments.get("collection")
        filters = arguments.get("filters", {})

        if not isinstance(collection, str):
            return self._invalid_arguments()
        if not isinstance(filters, dict):
            return ActionResult(
                ok=False,
                error="invalid_filter",
            )

        policy = self.database.get_policy(
            actor,
            collection,
            "query",
        )
        if policy is None:
            return self._permission_denied()

        allowed_fields = policy["allowed_fields"]

        if "*" not in allowed_fields:
            if not set(filters).issubset(allowed_fields):
                return self._permission_denied()

        records = self.database.list_records(collection)

        matched = [
            record
            for record in records
            if all(
                record["fields"].get(key) == value
                for key, value in filters.items()
            )
        ]

        visible = [
            self._project_record(
                record,
                allowed_fields,
            )
            for record in matched
        ]

        return ActionResult(
            ok=True,
            output={"records": visible},
        )

    def _update(
        self,
        actor: str,
        arguments: dict[str, Any],
    ) -> ActionResult:
        collection = arguments.get("collection")
        record_id = arguments.get("record_id")
        fields = arguments.get("fields")

        if not isinstance(collection, str):
            return self._invalid_arguments()
        if not isinstance(record_id, str):
            return self._invalid_arguments()
        if not isinstance(fields, dict) or not fields:
            return self._invalid_arguments()

        policy = self.database.get_policy(
            actor,
            collection,
            "update",
        )
        if policy is None:
            return self._permission_denied()

        allowed_fields = policy["allowed_fields"]

        if "*" not in allowed_fields:
            if not set(fields).issubset(allowed_fields):
                return self._permission_denied()

        try:
            updated = self.database.update_record(
                collection,
                record_id,
                fields,
            )
        except KeyError:
            return ActionResult(
                ok=False,
                error="record_not_found",
            )

        return ActionResult(
            ok=True,
            output={"record": updated},
            state_changed=True,
        )

    @staticmethod
    def _project_record(
        record: dict[str, Any],
        allowed_fields: list[str],
    ) -> dict[str, Any]:
        if "*" in allowed_fields:
            return record

        return {
            **record,
            "fields": {
                key: value
                for key, value in record["fields"].items()
                if key in allowed_fields
            },
        }

    @staticmethod
    def _permission_denied() -> ActionResult:
        return ActionResult(
            ok=False,
            error="permission_denied",
        )

    @staticmethod
    def _invalid_arguments() -> ActionResult:
        return ActionResult(
            ok=False,
            error="invalid_arguments",
        )
