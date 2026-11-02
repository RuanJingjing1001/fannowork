from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from fanno_work.models import ActionRequest
from fanno_work.specs import (
    load_environment,
    load_task,
)

from fanno_legal.runtime.observed_environment import (
    ObservedLegalOfficeEnvironment,
)


LEGAL_ROOT = Path(__file__).parents[2]

ENVIRONMENT_PATH = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)

TASK_PATH = (
    LEGAL_ROOT
    / "tasks"
    / "imported"
    / "harvey-lab"
    / "litigation-invoice-review"
    / "v1"
    / "task.json"
)

REQUIRED_IMAGES = (
    "fanno-legal-office:runtime-v0.7",
    "greenmail/standalone:2.1.8",
)


def images_available() -> bool:
    try:
        for image in REQUIRED_IMAGES:
            completed = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    image,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )

            if completed.returncode != 0:
                return False

        return True
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False


def assert_container_email_result(
    test: unittest.TestCase,
    output: dict,
) -> None:
    test.assertEqual(
        output["backend"],
        "greenmail",
    )
    test.assertEqual(
        output["execution_evidence"][
            "runtime"
        ],
        "docker",
    )
    test.assertEqual(
        output["execution_evidence"][
            "execution_scope"
        ],
        "task_container",
    )


@unittest.skipUnless(
    images_available(),
    "required Docker images unavailable",
)
class LitigationInvoiceEmailTests(
    unittest.TestCase
):
    def test_task_reads_source_email_from_greenmail(
        self,
    ) -> None:
        task = load_task(
            TASK_PATH,
            verify_assets=True,
        )
        environment_spec = load_environment(
            ENVIRONMENT_PATH
        )

        with tempfile.TemporaryDirectory() as directory:
            run_id = (
                "litigation-invoice-email-"
                + datetime.now(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%S%fZ"
                )
            )
            temporary_run_dir = (
                Path(directory) / "run"
            )
            saved_trace_dir = (
                LEGAL_ROOT
                / "runs"
                / "integration-tests"
                / "litigation-invoice-email"
                / run_id
            )

            environment = (
                ObservedLegalOfficeEnvironment(
                    environment_spec
                )
            )

            try:
                environment.prepare(
                    task,
                    temporary_run_dir,
                )

                workspace_eml = (
                    temporary_run_dir
                    / "workspace"
                    / "inputs"
                    / (
                        "holt-email-"
                        "research-concerns.eml"
                    )
                )

                self.assertFalse(
                    workspace_eml.exists()
                )

                search = environment.act(
                    ActionRequest(
                        name="email.search",
                        arguments={
                            "subject": (
                                "Research Billing "
                                "Concerns"
                            ),
                            "sender": (
                                "mholt@"
                                "terraverde-env.com"
                            ),
                            "max_results": 10,
                        },
                    )
                )

                self.assertTrue(
                    search.ok,
                    search.error,
                )
                self.assertFalse(
                    search.state_changed
                )
                assert_container_email_result(
                    self,
                    search.output,
                )

                messages = search.output[
                    "messages"
                ]

                self.assertEqual(
                    len(messages),
                    1,
                )

                read = environment.act(
                    ActionRequest(
                        name="email.read",
                        arguments={
                            "message_id": (
                                messages[0][
                                    "message_id"
                                ]
                            )
                        },
                    )
                )

                self.assertTrue(
                    read.ok,
                    read.error,
                )
                self.assertFalse(
                    read.state_changed
                )
                assert_container_email_result(
                    self,
                    read.output,
                )

                self.assertIn(
                    "12% cap",
                    read.output["body"],
                )
                self.assertIn(
                    "duplicative research",
                    read.output["body"],
                )
                self.assertIn(
                    "Section 6.6",
                    read.output["body"],
                )
                self.assertIn(
                    "Marcus Holt",
                    read.output["body"],
                )

                environment.reset()

                after_reset = environment.act(
                    ActionRequest(
                        name="email.search",
                        arguments={
                            "subject": (
                                "Research Billing "
                                "Concerns"
                            )
                        },
                    )
                )

                self.assertTrue(
                    after_reset.ok,
                    after_reset.error,
                )
                self.assertEqual(
                    len(
                        after_reset.output[
                            "messages"
                        ]
                    ),
                    1,
                )

            finally:
                environment.close()

                saved_trace_dir.mkdir(
                    parents=True,
                    exist_ok=False,
                )

                trace_source = (
                    temporary_run_dir
                    / "events.jsonl"
                )
                if trace_source.is_file():
                    shutil.copy2(
                        trace_source,
                        saved_trace_dir
                        / "events.jsonl",
                    )

                audit_source = (
                    temporary_run_dir
                    / "audit"
                    / "actions.jsonl"
                )
                if audit_source.is_file():
                    audit_target = (
                        saved_trace_dir
                        / "audit"
                        / "actions.jsonl"
                    )
                    audit_target.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    shutil.copy2(
                        audit_source,
                        audit_target,
                    )

                print(
                    "\nSaved trace:",
                    saved_trace_dir,
                    flush=True,
                )


if __name__ == "__main__":
    unittest.main()
