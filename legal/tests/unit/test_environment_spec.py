from pathlib import Path
import unittest

from fanno_work.specs import load_environment

from fanno_legal.capabilities.document import (
    DocumentCapability,
)
from fanno_legal.capabilities.email import (
    EmailCapability,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.capabilities.records import (
    RecordsCapability,
)
from fanno_legal.capabilities.spreadsheet import (
    SpreadsheetCapability,
)
from fanno_legal.capabilities.writer import (
    WriterCapability,
)
from fanno_legal.capabilities.calendar import (
    CalendarCapability,
)
from fanno_legal.contracts import load_action_contracts


LEGAL_ROOT = Path(__file__).parents[2]
ENV_DIR = LEGAL_ROOT / "environments" / "legal-office" / "v1"


class LegalEnvironmentSpecTests(unittest.TestCase):
    def test_legal_office_environment_can_be_loaded(self) -> None:
        environment = load_environment(ENV_DIR / "environment.json")

        self.assertEqual(environment.id, "legal.office.v1")
        self.assertEqual(environment.adapter, "legal_office")
        self.assertEqual(environment.version, "0.4.0")
        self.assertEqual(environment.source_dir, ENV_DIR.resolve())
        self.assertEqual(
            environment.metadata["capabilities"],
            [
                "filesystem",
                "document",
                "spreadsheet",
                "records",
                "email",
                "calendar",
            ],
        )
        self.assertEqual(
            environment.config["enabled_actions"],
            [
                "filesystem.list",
                "filesystem.read",
                "filesystem.write",
                "document.extract_text",
                "document.convert",
                "document.append_paragraph",
                "document.create",
                "document.insert_heading",
                "document.insert_paragraph",
                "document.add_table",
                "document.save_as",
                "spreadsheet.inspect_workbook",
                "spreadsheet.read_range",
                "spreadsheet.find",
                "records.get",
                "records.query",
                "records.update",
            ],
        )

    def test_referenced_files_exist(self) -> None:
        environment = load_environment(ENV_DIR / "environment.json")

        compose_file = (
            environment.source_dir
            / environment.config["compose_file"]
        )
        actions_file = (
            environment.source_dir
            / environment.config["actions_file"]
        )

        self.assertTrue(compose_file.is_file())
        self.assertTrue(actions_file.is_file())

    def test_action_contracts_can_be_loaded(self) -> None:
        environment = load_environment(ENV_DIR / "environment.json")
        contracts = load_action_contracts(environment)

        self.assertEqual(len(contracts), 26)
        self.assertEqual(
            [contract.id for contract in contracts],
            [
                "filesystem.list",
                "filesystem.read",
                "filesystem.write",
                "document.extract_text",
                "document.convert",
                "document.append_paragraph",
                "document.create",
                "document.insert_heading",
                "document.insert_paragraph",
                "document.add_table",
                "document.save_as",
                "spreadsheet.inspect_workbook",
                "spreadsheet.read_range",
                "spreadsheet.find",
                "email.search",
                "email.read",
                "email.download_attachment",
                "email.send",
                "calendar.list_calendars",
                "calendar.list_events",
                "calendar.find_events",
                "calendar.create_event",
                "calendar.delete_event",
                "records.get",
                "records.query",
                "records.update",
            ],
        )
        self.assertTrue(
            all(
                contract.input_schema["additionalProperties"] is False
                for contract in contracts
            )
        )

    def test_catalog_matches_runtime_implementations(
        self,
    ) -> None:
        environment = load_environment(
            ENV_DIR / "environment.json"
        )
        contracts = load_action_contracts(
            environment
        )

        declared = {
            contract.id
            for contract in contracts
        }
        implemented = set().union(
            FilesystemCapability.ACTION_NAMES,
            DocumentCapability.ACTION_NAMES,
            WriterCapability.ACTION_NAMES,
            SpreadsheetCapability.ACTION_NAMES,
            EmailCapability.ACTION_NAMES,
            CalendarCapability.ACTION_NAMES,
            RecordsCapability.ACTION_NAMES,
        )

        self.assertEqual(
            declared,
            implemented,
        )

    def test_contracts_generate_agent_descriptors(self) -> None:
        environment = load_environment(ENV_DIR / "environment.json")
        contracts = load_action_contracts(environment)

        descriptor = contracts[0].to_descriptor()

        self.assertEqual(descriptor.name, "filesystem.list")
        self.assertEqual(descriptor.input_schema["type"], "object")


if __name__ == "__main__":
    unittest.main()
