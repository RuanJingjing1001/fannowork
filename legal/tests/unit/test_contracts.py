import unittest

from fanno_legal.contracts import (
    ContractError,
    parse_action_contracts,
)


def valid_action() -> dict:
    return {
        "id": "records.get",
        "description": "Read one record",
        "input_schema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"}
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
        "effect": "read_only",
        "errors": ["record_not_found"],
    }


class ActionContractTests(unittest.TestCase):
    def test_duplicate_action_id_is_rejected(self) -> None:
        action = valid_action()

        with self.assertRaisesRegex(
            ContractError,
            "duplicate action id",
        ):
            parse_action_contracts(
                {
                    "schema_version": 1,
                    "actions": [action, dict(action)],
                },
                {"records"},
            )

    def test_undeclared_capability_is_rejected(self) -> None:
        action = valid_action()

        with self.assertRaisesRegex(
            ContractError,
            "undeclared capability",
        ):
            parse_action_contracts(
                {
                    "schema_version": 1,
                    "actions": [action],
                },
                {"filesystem"},
            )

    def test_unknown_required_property_is_rejected(self) -> None:
        action = valid_action()
        action["input_schema"]["required"] = ["missing"]

        with self.assertRaisesRegex(
            ContractError,
            "undefined properties",
        ):
            parse_action_contracts(
                {
                    "schema_version": 1,
                    "actions": [action],
                },
                {"records"},
            )
