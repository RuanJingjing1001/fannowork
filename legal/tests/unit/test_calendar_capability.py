from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fanno_work.models import ActionRequest
from fanno_legal.backends.calendar import CalendarBackendFailure
from fanno_legal.capabilities.calendar import CalendarCapability
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


class FakeCalendarBackend:
    backend_id = "fake-calendar"

    def __init__(self) -> None:
        self.events: dict[str, dict[str, dict]] = {
            "Matter deadlines": {}
        }

    def _evidence(self) -> dict:
        return {
            "backend": self.backend_id,
            "execution_evidence": {
                "execution_scope": "test",
                "runtime": "python",
                "timeout_seconds": 1,
            },
        }

    def list_calendars(self) -> dict:
        return {
            "calendars": [{"name": name} for name in self.events],
            "calendar_count": len(self.events),
            **self._evidence(),
        }

    def list_events(self, *, calendar, start, end) -> dict:
        if calendar not in self.events:
            raise CalendarBackendFailure("calendar_not_found")
        events = list(self.events[calendar].values())
        return {"events": events, "event_count": len(events), **self._evidence()}

    def find_events(self, *, calendar, query, max_results) -> dict:
        events = [
            value
            for value in self.events.get(calendar, {}).values()
            if query.casefold() in value["summary"].casefold()
        ][:max_results]
        return {"events": events, "event_count": len(events), **self._evidence()}

    def create_event(self, **arguments) -> dict:
        calendar = arguments.pop("calendar")
        if calendar not in self.events:
            raise CalendarBackendFailure("calendar_not_found")
        uid = arguments["uid"]
        if uid in self.events[calendar]:
            raise CalendarBackendFailure("event_already_exists")
        event = dict(arguments)
        self.events[calendar][uid] = event
        return {"event": event, "created": True, **self._evidence()}

    def delete_event(self, *, calendar, uid) -> dict:
        try:
            event = self.events[calendar].pop(uid)
        except KeyError as exc:
            raise CalendarBackendFailure("event_not_found") from exc
        return {"event": event, "deleted": True, **self._evidence()}


class CalendarCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = StateDatabase(root / "state.db")
        self.database.seed(
            {
                "schema_version": 1,
                "collections": [],
                "policies": [],
            }
        )
        self.backend = FakeCalendarBackend()
        self.capability = CalendarCapability(
            backend=self.backend,
            database=self.database,
            audit=AuditLog(root / "audit.jsonl"),
            config={
                "max_results": 20,
                "max_query_characters": 200,
                "max_summary_characters": 500,
                "max_description_characters": 2000,
            },
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_create_find_delete_and_errors(self) -> None:
        created = self.capability.act(
            "agent",
            ActionRequest(
                "calendar.create_event",
                {
                    "calendar": "Matter deadlines",
                    "uid": "hearing-001",
                    "summary": "Case management hearing",
                    "starts_at": "2026-11-10T09:00:00+08:00",
                    "ends_at": "2026-11-10T10:00:00+08:00",
                    "description": "Prepare status report",
                    "location": "Courtroom 3",
                },
            ),
        )
        self.assertTrue(created.ok, created.error)
        self.assertTrue(created.state_changed)
        self.assertEqual(self.database.get_state_version(), 1)

        found = self.capability.act(
            "agent",
            ActionRequest(
                "calendar.find_events",
                {"calendar": "Matter deadlines", "query": "hearing"},
            ),
        )
        self.assertTrue(found.ok, found.error)
        self.assertEqual(found.output["event_count"], 1)
        self.assertFalse(found.state_changed)

        invalid = self.capability.act(
            "agent",
            ActionRequest(
                "calendar.create_event",
                {
                    "calendar": "Matter deadlines",
                    "uid": "bad",
                    "summary": "Bad range",
                    "starts_at": "2026-11-10T10:00:00+08:00",
                    "ends_at": "2026-11-10T09:00:00+08:00",
                },
            ),
        )
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.error, "invalid_time_range")
        self.assertEqual(self.database.get_state_version(), 1)

        deleted = self.capability.act(
            "agent",
            ActionRequest(
                "calendar.delete_event",
                {"calendar": "Matter deadlines", "uid": "hearing-001"},
            ),
        )
        self.assertTrue(deleted.ok, deleted.error)
        self.assertEqual(self.database.get_state_version(), 2)
