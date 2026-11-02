from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from fanno_work.models import ActionRequest, TaskSpec, VerificationSpec
from fanno_work.observability import snapshot_diff
from fanno_work.specs import load_environment
from fanno_legal.backends.calendar import RadicaleCalendarClient
from fanno_legal.runtime.environment import LegalOfficeEnvironment


LEGAL_ROOT = Path(__file__).parents[2]
PROFILE = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)
RUNTIME_IMAGE = "fanno-legal-office:runtime-v0.7"
GREENMAIL_IMAGE = "greenmail/standalone:2.1.8"
RADICALE_IMAGE = (
    "tomsquest/docker-radicale@sha256:"
    "6a86c8fd17ff4a10b53487af8d635b68"
    "e7b8b38c8770320de56c9d44f45bbd73"
)


def images_available() -> bool:
    try:
        for image in (RUNTIME_IMAGE, GREENMAIL_IMAGE, RADICALE_IMAGE):
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def make_task() -> TaskSpec:
    seed = json.loads(
        (LEGAL_ROOT / "tests" / "fixtures" / "seed-a.json").read_text(
            encoding="utf-8"
        )
    )
    seed["email"] = {
        "accounts": {
            "agent": {
                "address": "agent@fanno.test",
                "password": "agent-password",
                "display_name": "Agent",
            }
        },
        "messages": [],
    }
    seed["calendar"] = {
        "calendars": [
            {
                "name": "Matter deadlines",
                "events": [
                    {
                        "uid": "seed-deadline-001",
                        "summary": "Disclosure deadline",
                        "starts_at": "2026-11-05T09:00:00+08:00",
                        "ends_at": "2026-11-05T09:30:00+08:00",
                        "description": "Serve disclosure list",
                        "location": "Online",
                    }
                ],
            }
        ]
    }
    return TaskSpec(
        schema_version=1,
        id="calendar.integration.v1",
        title="Calendar integration",
        instruction="Inspect and update the matter calendar.",
        environment_id="legal.office.v1",
        inputs=(),
        outputs=(),
        verifier=VerificationSpec(adapter="test"),
        initial_state=seed,
        source_dir=LEGAL_ROOT,
    )


@unittest.skipUnless(images_available(), "required Docker images unavailable")
class RadicaleCalendarEnvironmentTests(unittest.TestCase):
    def test_agent_create_independent_verify_snapshot_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = LegalOfficeEnvironment(load_environment(PROFILE))
            try:
                environment.prepare(make_task(), Path(directory) / "run")
                initial = environment.snapshot()

                listed = environment.act(
                    ActionRequest(
                        "calendar.list_events",
                        {"calendar": "Matter deadlines"},
                    )
                )
                self.assertTrue(listed.ok, listed.error)
                self.assertEqual(listed.output["event_count"], 1)
                self.assertEqual(
                    listed.output["execution_evidence"]["execution_scope"],
                    "task_container",
                )

                created = environment.act(
                    ActionRequest(
                        "calendar.create_event",
                        {
                            "calendar": "Matter deadlines",
                            "uid": "agent-hearing-002",
                            "summary": "Directions hearing",
                            "starts_at": "2026-11-12T14:00:00+08:00",
                            "ends_at": "2026-11-12T15:00:00+08:00",
                            "description": "Attend with draft directions",
                            "location": "Courtroom 4",
                        },
                    )
                )
                self.assertTrue(created.ok, created.error)
                self.assertTrue(created.state_changed)

                service = environment.radicale_service
                self.assertIsNotNone(service)
                independent = RadicaleCalendarClient(
                    url=service.host_url,
                    username="benchmark",
                    password="",
                    timeout_seconds=15,
                )
                try:
                    event = independent.get_event(
                        calendar="Matter deadlines",
                        uid="agent-hearing-002",
                    )
                finally:
                    independent.close()
                self.assertEqual(event["location"], "Courtroom 4")

                after_create = environment.snapshot()
                changes = snapshot_diff(initial, after_create)["calendar_changes"]
                self.assertEqual(len(changes), 1)
                self.assertEqual(changes[0]["change"], "created")

                environment.reset()
                after_reset = environment.snapshot()
                self.assertEqual(after_reset["calendars"], initial["calendars"])
                self.assertEqual(after_reset["state_version"], 0)
            finally:
                environment.close()
