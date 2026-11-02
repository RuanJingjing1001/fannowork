from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

from fanno_work.models import (
    ActionRequest,
    InputArtifact,
    TaskSpec,
    VerificationSpec,
)
from fanno_work.specs import load_environment

from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)


LEGAL_ROOT = Path(__file__).parents[2]
ENVIRONMENT_PATH = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)
TASK_SOURCE = (
    LEGAL_ROOT
    / "tasks"
    / "imported"
    / "harvey-lab"
    / "litigation-invoice-review"
    / "v1"
)
IMAGE = "fanno-legal-office:runtime-v0.7"


def docker_image_is_available() -> bool:
    try:
        info = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        image = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return info.returncode == 0 and image.returncode == 0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_task() -> TaskSpec:
    seed = json.loads(
        (
            LEGAL_ROOT
            / "tests"
            / "fixtures"
            / "seed-a.json"
        ).read_text(encoding="utf-8")
    )
    seed["email"] = {
        "accounts": {
            "agent": {
                "address": (
                    "legal.assistant@fanno.test"
                ),
                "password": "agent-password",
                "display_name": (
                    "Legal Assistant"
                ),
            }
        },
        "messages": [],
    }
    return TaskSpec(
        schema_version=1,
        id="legal.container-action-contract.v1",
        title="Container action contract test",
        instruction="Read inputs and create a review document.",
        environment_id="legal.office.v1",
        inputs=(
            InputArtifact(
                path="invoice.xlsx",
                source="assets/hl-july-2024-invoice.xlsx",
            ),
            InputArtifact(
                path="message.eml",
                source="assets/holt-email-research-concerns.eml",
            ),
        ),
        outputs=(),
        verifier=VerificationSpec(adapter="test"),
        initial_state=seed,
        source_dir=TASK_SOURCE,
    )


@unittest.skipUnless(
    docker_image_is_available(),
    f"Docker image {IMAGE} is unavailable",
)
class ContainerActionContractTests(unittest.TestCase):
    def test_filesystem_writer_calc_and_reset_share_one_container(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = load_environment(ENVIRONMENT_PATH)
            self.assertEqual(
                spec.config["execution"]["backend"],
                "container",
            )

            environment = LegalOfficeEnvironment(spec)
            try:
                environment.prepare(
                    make_task(),
                    root / "run",
                )
                workspace = root / "run" / "workspace"
                input_hash = sha256(
                    workspace / "inputs" / "invoice.xlsx"
                )
                initial = environment.snapshot()

                read_result = environment.act(
                    ActionRequest(
                        "filesystem.read",
                        {"path": "inputs/message.eml"},
                    )
                )
                self.assertTrue(read_result.ok)
                self.assertEqual(
                    read_result.output["backend"],
                    "container-filesystem",
                )
                self.assertIn(
                    "Research Billing",
                    read_result.output["content"],
                )

                write_result = environment.act(
                    ActionRequest(
                        "filesystem.write",
                        {
                            "path": "work/note.txt",
                            "content": "container filesystem contract",
                        },
                    )
                )
                self.assertTrue(write_result.ok)
                self.assertEqual(
                    (workspace / "work" / "note.txt").read_text(
                        encoding="utf-8"
                    ),
                    "container filesystem contract",
                )

                extraction_result = environment.act(
                    ActionRequest(
                        "document.extract_text",
                        {"path": "work/note.txt"},
                    )
                )
                self.assertTrue(
                    extraction_result.ok,
                    extraction_result.error,
                )
                self.assertEqual(
                    extraction_result.output["text"],
                    "container filesystem contract",
                )
                self.assertEqual(
                    extraction_result.output[
                        "execution_evidence"
                    ]["execution_scope"],
                    "task_container",
                )

                conversion_result = environment.act(
                    ActionRequest(
                        "document.convert",
                        {
                            "source_path": "work/note.txt",
                            "target_path": "outputs/note.pdf",
                            "format": "pdf",
                        },
                    )
                )
                self.assertTrue(
                    conversion_result.ok,
                    conversion_result.error,
                )
                self.assertEqual(
                    conversion_result.output[
                        "execution_evidence"
                    ]["execution_scope"],
                    "task_container",
                )
                self.assertTrue(
                    (workspace / "outputs" / "note.pdf").is_file()
                )

                calc_result = environment.act(
                    ActionRequest(
                        "spreadsheet.inspect_workbook",
                        {"path": "inputs/invoice.xlsx"},
                    )
                )
                self.assertTrue(calc_result.ok)
                self.assertEqual(
                    calc_result.output["execution_evidence"][
                        "execution_scope"
                    ],
                    "task_container",
                )
                self.assertEqual(
                    calc_result.output["execution_evidence"][
                        "runtime"
                    ],
                    "docker",
                )
                self.assertIn(
                    "Fee Detail",
                    [
                        item["name"]
                        for item in calc_result.output["sheets"]
                    ],
                )

                create_result = environment.act(
                    ActionRequest(
                        "document.create",
                        {
                            "path": "outputs/review.odt",
                            "title": "Invoice Review",
                        },
                    )
                )
                self.assertTrue(create_result.ok)
                self.assertEqual(
                    create_result.output["execution_evidence"][
                        "execution_scope"
                    ],
                    "task_container",
                )

                heading_result = environment.act(
                    ActionRequest(
                        "document.insert_heading",
                        {
                            "path": "outputs/review.odt",
                            "text": "Executive Summary",
                            "level": 1,
                        },
                    )
                )
                self.assertTrue(heading_result.ok)

                paragraph_result = environment.act(
                    ActionRequest(
                        "document.insert_paragraph",
                        {
                            "path": "outputs/review.odt",
                            "text": (
                                "The invoice requires an "
                                "independent legal review."
                            ),
                            "style": "Body Text",
                        },
                    )
                )
                self.assertTrue(
                    paragraph_result.ok,
                    paragraph_result.error,
                )
                self.assertTrue(
                    paragraph_result.state_changed
                )

                table_result = environment.act(
                    ActionRequest(
                        "document.add_table",
                        {
                            "path": "outputs/review.odt",
                            "headers": [
                                "Issue",
                                "Adjustment",
                            ],
                            "rows": [
                                [
                                    "Research charge",
                                    "$4,850.00",
                                ]
                            ],
                        },
                    )
                )
                self.assertTrue(
                    table_result.ok,
                    table_result.error,
                )
                self.assertTrue(
                    table_result.state_changed
                )

                save_result = environment.act(
                    ActionRequest(
                        "document.save_as",
                        {
                            "source_path": "outputs/review.odt",
                            "target_path": "outputs/review.docx",
                        },
                    )
                )
                self.assertTrue(save_result.ok)

                docx = workspace / "outputs" / "review.docx"
                self.assertTrue(docx.is_file())
                with ZipFile(docx) as archive:
                    document_xml = archive.read(
                        "word/document.xml"
                    ).decode("utf-8")
                self.assertIn("Executive Summary", document_xml)
                self.assertIn(
                    "independent legal review",
                    document_xml,
                )
                self.assertIn(
                    "Research charge",
                    document_xml,
                )
                self.assertIn(
                    "$4,850.00",
                    document_xml,
                )
                self.assertIn(
                    "<w:tbl",
                    document_xml,
                )

                append_result = environment.act(
                    ActionRequest(
                        "document.append_paragraph",
                        {
                            "source_path": (
                                "outputs/review.docx"
                            ),
                            "target_path": (
                                "outputs/review-amended.docx"
                            ),
                            "text": (
                                "Final approval remains required."
                            ),
                        },
                    )
                )
                self.assertTrue(
                    append_result.ok,
                    append_result.error,
                )
                self.assertEqual(
                    append_result.output[
                        "execution_evidence"
                    ]["execution_scope"],
                    "task_container",
                )

                amended = (
                    workspace
                    / "outputs"
                    / "review-amended.docx"
                )
                with ZipFile(amended) as archive:
                    amended_xml = archive.read(
                        "word/document.xml"
                    ).decode("utf-8")

                self.assertIn(
                    "Final approval remains required.",
                    amended_xml,
                )
                self.assertIn(
                    "Executive Summary",
                    amended_xml,
                )

                before_denied = environment.snapshot()
                denied_result = environment.act(
                    ActionRequest(
                        "document.create",
                        {
                            "path": "inputs/forbidden.odt",
                            "title": "Forbidden",
                        },
                    )
                )
                after_denied = environment.snapshot()

                self.assertFalse(denied_result.ok)
                self.assertEqual(
                    denied_result.error,
                    "permission_denied",
                )
                self.assertEqual(
                    after_denied["digest"],
                    before_denied["digest"],
                )
                self.assertEqual(
                    sha256(workspace / "inputs" / "invoice.xlsx"),
                    input_hash,
                )

                environment.reset()
                self.assertEqual(
                    environment.snapshot()["digest"],
                    initial["digest"],
                )
                self.assertFalse(docx.exists())
                self.assertFalse(amended.exists())

                post_reset = environment.act(
                    ActionRequest(
                        "filesystem.list",
                        {"path": "inputs"},
                    )
                )
                self.assertTrue(post_reset.ok)
            finally:
                environment.close()


if __name__ == "__main__":
    unittest.main()
