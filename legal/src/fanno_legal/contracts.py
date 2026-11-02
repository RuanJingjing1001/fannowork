from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from fanno_work.models import ActionDescriptor, EnvironmentSpec


_ACTION_ID = re.compile(
    r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
)


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ActionContract:
    id: str
    description: str
    input_schema: dict[str, Any]
    effect: str
    errors: tuple[str, ...]

    def to_descriptor(self) -> ActionDescriptor:
        return ActionDescriptor(
            name=self.id,
            description=self.description,
            input_schema=self.input_schema,
        )


def _required_string(
    data: dict[str, Any],
    key: str,
    context: str,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            f"{context}.{key} must be a non-empty string"
        )
    return value


def _parse_input_schema(
    value: Any,
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(
            f"{context}.input_schema must be an object"
        )

    if value.get("type") != "object":
        raise ContractError(
            f"{context}.input_schema.type must be 'object'"
        )

    properties = value.get("properties")
    if not isinstance(properties, dict):
        raise ContractError(
            f"{context}.input_schema.properties must be an object"
        )

    required = value.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(item, str) for item in required
    ):
        raise ContractError(
            f"{context}.input_schema.required "
            "must be a string list"
        )

    unknown_required = set(required) - set(properties)
    if unknown_required:
        raise ContractError(
            f"{context} requires undefined properties: "
            f"{sorted(unknown_required)}"
        )

    if value.get("additionalProperties") is not False:
        raise ContractError(
            f"{context}.input_schema.additionalProperties "
            "must be false"
        )

    return dict(value)


def parse_action_contracts(
    data: dict[str, Any],
    allowed_capabilities: set[str],
) -> tuple[ActionContract, ...]:
    if data.get("schema_version") != 1:
        raise ContractError(
            "unsupported action contract schema_version"
        )

    raw_actions = data.get("actions")
    if not isinstance(raw_actions, list):
        raise ContractError("actions must be a list")

    contracts: list[ActionContract] = []
    seen: set[str] = set()

    for index, raw in enumerate(raw_actions):
        context = f"actions[{index}]"

        if not isinstance(raw, dict):
            raise ContractError(
                f"{context} must be an object"
            )

        action_id = _required_string(
            raw,
            "id",
            context,
        )
        if not _ACTION_ID.fullmatch(action_id):
            raise ContractError(
                f"invalid action id: {action_id!r}"
            )
        if action_id in seen:
            raise ContractError(
                f"duplicate action id: {action_id}"
            )

        capability = action_id.split(".", 1)[0]
        if capability not in allowed_capabilities:
            raise ContractError(
                f"action {action_id!r} uses undeclared "
                f"capability {capability!r}"
            )

        description = _required_string(
            raw,
            "description",
            context,
        )
        effect = _required_string(
            raw,
            "effect",
            context,
        )
        input_schema = _parse_input_schema(
            raw.get("input_schema"),
            context,
        )

        errors = raw.get("errors")
        if not isinstance(errors, list) or not all(
            isinstance(item, str) and item
            for item in errors
        ):
            raise ContractError(
                f"{context}.errors must be a string list"
            )
        if len(errors) != len(set(errors)):
            raise ContractError(
                f"{context}.errors contains duplicates"
            )

        contracts.append(
            ActionContract(
                id=action_id,
                description=description,
                input_schema=input_schema,
                effect=effect,
                errors=tuple(errors),
            )
        )
        seen.add(action_id)

    return tuple(contracts)


def load_action_contracts(
    environment: EnvironmentSpec,
) -> tuple[ActionContract, ...]:
    relative = environment.config.get("actions_file")
    if not isinstance(relative, str) or not relative:
        raise ContractError(
            "environment config.actions_file is required"
        )

    root = environment.source_dir.resolve()
    path = (root / relative).resolve()

    if not path.is_relative_to(root):
        raise ContractError(
            f"actions file escapes environment directory: "
            f"{relative}"
        )
    if not path.is_file():
        raise ContractError(
            f"actions file does not exist: {relative}"
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(
            f"cannot read action contracts: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ContractError(
            "action contract file must contain an object"
        )

    capabilities = environment.metadata.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise ContractError(
            "environment metadata.capabilities "
            "must be a string list"
        )

    return parse_action_contracts(
        data,
        set(capabilities),
    )