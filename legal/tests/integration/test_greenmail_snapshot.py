from __future__ import annotations

from email.message import EmailMessage
import hashlib
import imaplib
from pathlib import Path
import smtplib
import tempfile
import unittest

from fanno_work.observability import (
    snapshot_diff,
)
from fanno_work.specs import (
    load_environment,
)

from fanno_legal.backends.email import (
    parse_email_seed,
)
from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)

from legal.tests.integration.test_greenmail_environment import (
    PROFILE,
    images_available,
    make_task,
)


@unittest.skipUnless(
    images_available(),
    "required Docker images unavailable",
)
class GreenMailSnapshotTests(
    unittest.TestCase
):
    def test_snapshot_tracks_add_flags_delete_and_reset(
        self,
    ) -> None:
        content = (
            b"mailbox snapshot attachment\n"
        )

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

                service = (
                    environment
                    .greenmail_service
                )
                self.assertIsNotNone(service)

                seed = parse_email_seed(
                    task.initial_state
                )
                agent = seed.accounts[
                    "agent"
                ]
                client = seed.accounts[
                    "client"
                ]

                initial = (
                    environment.snapshot()
                )

                initial_agent = (
                    initial["mailboxes"]
                    ["accounts"]["agent"]
                )

                self.assertEqual(
                    initial_agent["address"],
                    agent.address,
                )
                self.assertEqual(
                    initial_agent["folders"]
                    ["INBOX"]["message_count"],
                    1,
                )

                message = EmailMessage()
                message["From"] = (
                    client.address
                )
                message["To"] = (
                    agent.address
                )
                message["Subject"] = (
                    "Snapshot attachment"
                )
                message["Message-ID"] = (
                    "<snapshot-attachment"
                    "@fanno.test>"
                )
                message.set_content(
                    "Inspect the attached evidence."
                )
                message.add_attachment(
                    content,
                    maintype="text",
                    subtype="plain",
                    filename="evidence.txt",
                )

                with smtplib.SMTP(
                    "127.0.0.1",
                    service.smtp_host_port,
                    timeout=15,
                ) as smtp:
                    smtp.send_message(
                        message
                    )

                after_create = (
                    environment.snapshot()
                )

                created_diff = snapshot_diff(
                    initial,
                    after_create,
                )

                created_changes = [
                    item
                    for item in (
                        created_diff[
                            "mailbox_changes"
                        ]
                    )
                    if item["change"]
                    == "created"
                ]

                self.assertEqual(
                    len(created_changes),
                    1,
                )

                created_message = (
                    created_changes[0][
                        "after"
                    ]
                )

                self.assertEqual(
                    created_message[
                        "message_id"
                    ],
                    (
                        "<snapshot-attachment"
                        "@fanno.test>"
                    ),
                )
                self.assertEqual(
                    created_message[
                        "attachments"
                    ][0]["sha256"],
                    hashlib.sha256(
                        content
                    ).hexdigest(),
                )

                connection = imaplib.IMAP4(
                    "127.0.0.1",
                    service.imap_host_port,
                    timeout=15,
                )

                try:
                    connection.login(
                        agent.address,
                        agent.password,
                    )
                    connection.select(
                        "INBOX"
                    )

                    status, data = (
                        connection.uid(
                            "search",
                            None,
                            "ALL",
                        )
                    )
                    self.assertEqual(
                        status,
                        "OK",
                    )

                    uid = data[0].split()[-1]

                    status, _ = connection.uid(
                        "store",
                        uid,
                        "+FLAGS",
                        "(\\Flagged)",
                    )
                    self.assertEqual(
                        status,
                        "OK",
                    )
                finally:
                    try:
                        connection.logout()
                    except Exception:
                        connection.shutdown()

                after_flag = (
                    environment.snapshot()
                )

                flag_diff = snapshot_diff(
                    after_create,
                    after_flag,
                )

                modified = [
                    item
                    for item in (
                        flag_diff[
                            "mailbox_changes"
                        ]
                    )
                    if item["change"]
                    == "modified"
                ]

                self.assertEqual(
                    len(modified),
                    1,
                )
                self.assertIn(
                    "\\Flagged",
                    modified[0]["after"][
                        "flags"
                    ],
                )

                connection = imaplib.IMAP4(
                    "127.0.0.1",
                    service.imap_host_port,
                    timeout=15,
                )

                try:
                    connection.login(
                        agent.address,
                        agent.password,
                    )
                    connection.select(
                        "INBOX"
                    )

                    status, data = (
                        connection.uid(
                            "search",
                            None,
                            "ALL",
                        )
                    )
                    self.assertEqual(
                        status,
                        "OK",
                    )

                    uid = data[0].split()[-1]

                    status, _ = connection.uid(
                        "store",
                        uid,
                        "+FLAGS",
                        "(\\Deleted)",
                    )
                    self.assertEqual(
                        status,
                        "OK",
                    )

                    status, _ = (
                        connection.expunge()
                    )
                    self.assertEqual(
                        status,
                        "OK",
                    )
                finally:
                    try:
                        connection.logout()
                    except Exception:
                        connection.shutdown()

                after_delete = (
                    environment.snapshot()
                )

                delete_diff = snapshot_diff(
                    after_flag,
                    after_delete,
                )

                deleted = [
                    item
                    for item in (
                        delete_diff[
                            "mailbox_changes"
                        ]
                    )
                    if item["change"]
                    == "deleted"
                ]

                self.assertEqual(
                    len(deleted),
                    1,
                )

                environment.reset()

                restored = (
                    environment.snapshot()
                )

                self.assertEqual(
                    restored["digest"],
                    initial["digest"],
                )
                self.assertEqual(
                    snapshot_diff(
                        initial,
                        restored,
                    )["mailbox_changes"],
                    [],
                )

            finally:
                environment.close()


if __name__ == "__main__":
    unittest.main()