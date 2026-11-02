from __future__ import annotations

from email.message import EmailMessage
import hashlib
import json
from pathlib import Path
import smtplib
import subprocess
import tempfile
import unittest

from fanno_work.models import (
    ActionRequest,
    TaskSpec,
    VerificationSpec,
)
from fanno_work.specs import load_environment

from fanno_legal.backends.email import (
    GreenMailClient,
    parse_email_seed,
)
from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)


LEGAL_ROOT = Path(__file__).parents[2]

PROFILE = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)

RUNTIME_IMAGE = (
    "fanno-legal-office:runtime-v0.7"
)
GREENMAIL_IMAGE = (
    "greenmail/standalone:2.1.8"
)


def images_available() -> bool:
    try:
        for image in (
            RUNTIME_IMAGE,
            GREENMAIL_IMAGE,
        ):
            result = subprocess.run(
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

            if result.returncode != 0:
                return False

        return True
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False


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
            },
            "client": {
                "address": "client@fanno.test",
                "password": "client-password",
                "display_name": "Client",
            },
            "lawyer": {
                "address": "lawyer@fanno.test",
                "password": "lawyer-password",
                "display_name": (
                    "External Counsel"
                ),
            },
        },
        "messages": [
            {
                "from_account": "client",
                "to_accounts": ["agent"],
                "subject": (
                    "Invoice review instructions"
                ),
                "body": (
                    "Review duplicative research "
                    "charges."
                ),
                "date": (
                    "Thu, 29 Oct 2026 "
                    "09:00:00 +0800"
                ),
            }
        ],
    }

    return TaskSpec(
        schema_version=1,
        id="greenmail.integration.v1",
        title="GreenMail integration",
        instruction="Read and send email.",
        environment_id="legal.office.v1",
        inputs=(),
        outputs=(),
        verifier=VerificationSpec(
            adapter="test"
        ),
        initial_state=seed,
        source_dir=LEGAL_ROOT,
    )


@unittest.skipUnless(
    images_available(),
    "required Docker images unavailable",
)
class GreenMailEnvironmentTests(
    unittest.TestCase
):
    def assert_container_execution(
        self,
        output: dict,
    ) -> None:
        self.assertEqual(
            output["backend"],
            "greenmail",
        )
        self.assertEqual(
            output["execution_evidence"][
                "execution_scope"
            ],
            "task_container",
        )
        self.assertEqual(
            output["execution_evidence"][
                "runtime"
            ],
            "docker",
        )

    def test_search_send_independent_verify_and_reset(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = (
                LegalOfficeEnvironment(
                    load_environment(PROFILE)
                )
            )
            task = make_task()

            try:
                environment.prepare(
                    task,
                    Path(directory) / "run",
                )

                task_container = (
                    environment.container_workspace
                )
                service = (
                    environment.greenmail_service
                )

                self.assertIsNotNone(
                    task_container
                )
                self.assertIsNotNone(service)
                self.assertTrue(
                    task_container.running
                )
                self.assertTrue(service.running)
                self.assertEqual(
                    task_container.network,
                    service.network_name,
                )

                search = environment.act(
                    ActionRequest(
                        "email.search",
                        {
                            "subject": (
                                "Invoice review"
                            )
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
                self.assert_container_execution(
                    search.output
                )
                self.assertEqual(
                    len(
                        search.output[
                            "messages"
                        ]
                    ),
                    1,
                )

                message_id = (
                    search.output["messages"][0][
                        "message_id"
                    ]
                )

                read = environment.act(
                    ActionRequest(
                        "email.read",
                        {
                            "message_id": message_id
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
                self.assert_container_execution(
                    read.output
                )
                self.assertIn(
                    "duplicative research",
                    read.output["body"],
                )

                sent = environment.act(
                    ActionRequest(
                        "email.send",
                        {
                            "to": [
                                "lawyer@fanno.test"
                            ],
                            "subject": (
                                "Research charge review"
                            ),
                            "body": (
                                "Please review the "
                                "identified charges."
                            ),
                        },
                    )
                )

                self.assertTrue(
                    sent.ok,
                    sent.error,
                )
                self.assertTrue(
                    sent.state_changed
                )
                self.assert_container_execution(
                    sent.output
                )

                service = (
                    environment
                    .greenmail_service
                )
                seed = parse_email_seed(
                    task.initial_state
                )

                independent = GreenMailClient(
                    host="127.0.0.1",
                    imap_port=(
                        service.imap_host_port
                    ),
                    smtp_port=(
                        service.smtp_host_port
                    ),
                    account=(
                        seed.accounts["lawyer"]
                    ),
                )

                received = independent.search(
                    subject=(
                        "Research charge review"
                    )
                )

                self.assertEqual(
                    len(received),
                    1,
                )

                task_container_before = (
                    environment
                    .container_workspace
                    .container_name
                )
                greenmail_container_before = (
                    environment
                    .greenmail_service
                    .container_name
                )

                environment.reset()

                self.assertTrue(
                    environment
                    .container_workspace
                    .running
                )
                self.assertTrue(
                    environment
                    .greenmail_service
                    .running
                )
                self.assertNotEqual(
                    environment
                    .container_workspace
                    .container_name,
                    task_container_before,
                )
                self.assertNotEqual(
                    environment
                    .greenmail_service
                    .container_name,
                    greenmail_container_before,
                )
                self.assertEqual(
                    environment
                    .container_workspace
                    .network,
                    environment
                    .greenmail_service
                    .network_name,
                )

                service = (
                    environment
                    .greenmail_service
                )

                after_reset = GreenMailClient(
                    host="127.0.0.1",
                    imap_port=(
                        service.imap_host_port
                    ),
                    smtp_port=(
                        service.smtp_host_port
                    ),
                    account=(
                        seed.accounts["lawyer"]
                    ),
                )

                self.assertEqual(
                    after_reset.search(
                        subject=(
                            "Research charge review"
                        )
                    ),
                    [],
                )

                agent_after_reset = (
                    GreenMailClient(
                        host="127.0.0.1",
                        imap_port=(
                            service.imap_host_port
                        ),
                        smtp_port=(
                            service.smtp_host_port
                        ),
                        account=(
                            seed.accounts["agent"]
                        ),
                    )
                )

                self.assertEqual(
                    len(
                        agent_after_reset.search(
                            subject=(
                                "Invoice review"
                            )
                        )
                    ),
                    1,
                )

            finally:
                environment.close()

    def test_download_attachment_into_workspace(
        self,
    ) -> None:
        content = b"invoice attachment evidence\n"

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            environment = LegalOfficeEnvironment(
                load_environment(PROFILE)
            )
            task = make_task()

            try:
                environment.prepare(
                    task,
                    run_dir,
                )
                service = environment.greenmail_service
                self.assertIsNotNone(service)

                seed = parse_email_seed(
                    task.initial_state
                )
                message = EmailMessage()
                message["From"] = (
                    seed.accounts["client"].address
                )
                message["To"] = (
                    seed.accounts["agent"].address
                )
                message["Subject"] = (
                    "Invoice attachment"
                )
                message.set_content(
                    "Please inspect the attachment."
                )
                message.add_attachment(
                    content,
                    maintype="text",
                    subtype="plain",
                    filename="invoice.txt",
                )

                with smtplib.SMTP(
                    "127.0.0.1",
                    service.smtp_host_port,
                    timeout=15,
                ) as smtp:
                    smtp.send_message(message)

                search = environment.act(
                    ActionRequest(
                        "email.search",
                        {
                            "subject": (
                                "Invoice attachment"
                            )
                        },
                    )
                )
                self.assertTrue(
                    search.ok,
                    search.error,
                )
                message_id = (
                    search.output["messages"][0][
                        "message_id"
                    ]
                )

                read = environment.act(
                    ActionRequest(
                        "email.read",
                        {"message_id": message_id},
                    )
                )
                self.assertTrue(read.ok, read.error)
                attachments = read.output[
                    "attachments"
                ]
                self.assertEqual(len(attachments), 1)
                self.assertEqual(
                    attachments[0]["filename"],
                    "invoice.txt",
                )
                self.assertEqual(
                    attachments[0]["sha256"],
                    hashlib.sha256(
                        content
                    ).hexdigest(),
                )

                request = ActionRequest(
                    "email.download_attachment",
                    {
                        "message_id": message_id,
                        "attachment_id": (
                            attachments[0][
                                "attachment_id"
                            ]
                        ),
                        "target_path": (
                            "work/email-attachments/"
                            "invoice.txt"
                        ),
                    },
                )
                downloaded = environment.act(request)

                self.assertTrue(
                    downloaded.ok,
                    downloaded.error,
                )
                self.assertTrue(
                    downloaded.state_changed
                )
                self.assert_container_execution(
                    downloaded.output
                )
                output_path = (
                    run_dir
                    / "workspace"
                    / "work"
                    / "email-attachments"
                    / "invoice.txt"
                )
                self.assertEqual(
                    output_path.read_bytes(),
                    content,
                )

                repeated = environment.act(request)
                self.assertTrue(
                    repeated.ok,
                    repeated.error,
                )
                self.assertFalse(
                    repeated.state_changed
                )

                environment.reset()
                self.assertFalse(output_path.exists())

                after_reset = environment.act(
                    ActionRequest(
                        "email.search",
                        {
                            "subject": (
                                "Invoice attachment"
                            )
                        },
                    )
                )
                self.assertEqual(
                    after_reset.output["messages"],
                    [],
                )
            finally:
                environment.close()


if __name__ == "__main__":
    unittest.main()
