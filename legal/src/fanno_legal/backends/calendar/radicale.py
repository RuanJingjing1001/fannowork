from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from typing import Any

import caldav

from .base import CalendarBackendFailure


@dataclass(frozen=True)
class CalendarSeed:
    calendars: tuple[dict[str, Any], ...]


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"calendar event {field} must be an ISO datetime")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"calendar event {field} must be an ISO datetime"
        ) from exc
    return result


def parse_calendar_seed(initial_state: dict[str, Any]) -> CalendarSeed:
    raw_calendar = initial_state.get("calendar", {})
    if not isinstance(raw_calendar, dict):
        raise ValueError("initial_state.calendar must be an object")

    raw_calendars = raw_calendar.get("calendars", [])
    if not isinstance(raw_calendars, list):
        raise ValueError("initial_state.calendar.calendars must be a list")

    names: set[str] = set()
    calendars: list[dict[str, Any]] = []

    for calendar_index, value in enumerate(raw_calendars):
        if not isinstance(value, dict):
            raise ValueError(f"calendar {calendar_index} must be an object")
        name = value.get("name")
        events = value.get("events", [])
        if not isinstance(name, str) or not name:
            raise ValueError(f"calendar {calendar_index} name is invalid")
        if name in names:
            raise ValueError(f"duplicate calendar name: {name}")
        if not isinstance(events, list):
            raise ValueError(f"calendar {name} events must be a list")
        names.add(name)

        normalized_events: list[dict[str, str]] = []
        uids: set[str] = set()
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                raise ValueError(
                    f"calendar {name} event {event_index} must be an object"
                )
            summary = event.get("summary")
            if not isinstance(summary, str) or not summary:
                raise ValueError(
                    f"calendar {name} event {event_index} summary is invalid"
                )
            starts_at = _parse_datetime(event.get("starts_at"), "starts_at")
            ends_at = _parse_datetime(event.get("ends_at"), "ends_at")
            if ends_at <= starts_at:
                raise ValueError("calendar event ends_at must be after starts_at")

            uid = event.get("uid")
            if uid is None:
                digest = hashlib.sha256(
                    (
                        name
                        + "\0"
                        + summary
                        + "\0"
                        + starts_at.isoformat()
                        + "\0"
                        + ends_at.isoformat()
                    ).encode("utf-8")
                ).hexdigest()[:24]
                uid = f"seed-{digest}@fanno.local"
            if not isinstance(uid, str) or not uid or uid in uids:
                raise ValueError(f"invalid or duplicate calendar event uid: {uid!r}")
            uids.add(uid)

            description = event.get("description", "")
            location = event.get("location", "")
            if not isinstance(description, str) or not isinstance(location, str):
                raise ValueError("calendar event description/location must be strings")

            normalized_events.append(
                {
                    "uid": uid,
                    "summary": summary,
                    "starts_at": starts_at.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "description": description,
                    "location": location,
                }
            )

        calendars.append({"name": name, "events": normalized_events})

    return CalendarSeed(calendars=tuple(calendars))


def _component_value(component: Any, name: str) -> str:
    value = component.get(name)
    return "" if value is None else str(value)


def _component_datetime(component: Any, name: str) -> str | None:
    value = component.get(name)
    if value is None:
        return None
    decoded = getattr(value, "dt", value)
    if isinstance(decoded, (datetime, date)):
        return decoded.isoformat()
    return str(decoded)


def event_to_dict(event: Any) -> dict[str, Any]:
    component = event.icalendar_component
    result: dict[str, Any] = {
        "uid": _component_value(component, "uid"),
        "summary": _component_value(component, "summary"),
        "description": _component_value(component, "description"),
        "location": _component_value(component, "location"),
    }
    starts_at = _component_datetime(component, "dtstart")
    ends_at = _component_datetime(component, "dtend")
    if starts_at is not None:
        result["starts_at"] = starts_at
    if ends_at is not None:
        result["ends_at"] = ends_at
    return result


class RadicaleCalendarClient:
    """Synchronous trusted/worker CalDAV client for one Radicale principal."""

    def __init__(
        self,
        *,
        url: str,
        username: str,
        password: str = "",
        timeout_seconds: int = 15,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.url = url
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds
        try:
            self._client = caldav.DAVClient(
                url=url,
                username=username,
                password=password,
                timeout=timeout_seconds,
            )
            self._principal = self._client.principal()
        except Exception as exc:
            raise CalendarBackendFailure(
                "calendar_connection_failed",
                str(exc),
                retryable=True,
            ) from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def list_calendars(self) -> list[dict[str, str]]:
        try:
            return [
                {"name": str(calendar.name)}
                for calendar in self._principal.calendars()
            ]
        except Exception as exc:
            raise CalendarBackendFailure("calendar_list_failed", str(exc)) from exc

    def _calendar(self, name: str) -> Any:
        try:
            for calendar in self._principal.calendars():
                if str(calendar.name) == name:
                    return calendar
        except Exception as exc:
            raise CalendarBackendFailure("calendar_list_failed", str(exc)) from exc
        raise CalendarBackendFailure("calendar_not_found", name)

    def create_calendar(self, name: str) -> None:
        if any(item["name"] == name for item in self.list_calendars()):
            raise CalendarBackendFailure("calendar_already_exists", name)
        try:
            self._principal.make_calendar(name=name)
        except Exception as exc:
            raise CalendarBackendFailure("calendar_create_failed", str(exc)) from exc

    def delete_all_calendars(self) -> None:
        try:
            calendars = list(self._principal.calendars())
            for calendar in calendars:
                calendar.delete()
        except Exception as exc:
            raise CalendarBackendFailure("calendar_reset_failed", str(exc)) from exc

    def list_events(
        self,
        *,
        calendar: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        target = self._calendar(calendar)
        try:
            if start is not None or end is not None:
                if start is None or end is None:
                    raise CalendarBackendFailure("invalid_time_range")
                events = target.search(
                    event=True,
                    start=datetime.fromisoformat(start),
                    end=datetime.fromisoformat(end),
                    expand=True,
                )
            else:
                events = target.events()
            return sorted(
                (event_to_dict(event) for event in events),
                key=lambda item: (
                    str(item.get("starts_at", "")),
                    str(item.get("uid", "")),
                ),
            )
        except CalendarBackendFailure:
            raise
        except ValueError as exc:
            raise CalendarBackendFailure("invalid_time_range", str(exc)) from exc
        except Exception as exc:
            raise CalendarBackendFailure("calendar_read_failed", str(exc)) from exc

    def create_event(
        self,
        *,
        calendar: str,
        uid: str,
        summary: str,
        starts_at: str,
        ends_at: str,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        target = self._calendar(calendar)
        try:
            try:
                target.event_by_uid(uid)
            except Exception:
                pass
            else:
                raise CalendarBackendFailure("event_already_exists", uid)

            target.save_event(
                uid=uid,
                summary=summary,
                dtstart=datetime.fromisoformat(starts_at),
                dtend=datetime.fromisoformat(ends_at),
                description=description,
                location=location,
            )
            return self.get_event(calendar=calendar, uid=uid)
        except CalendarBackendFailure:
            raise
        except ValueError as exc:
            raise CalendarBackendFailure("invalid_datetime", str(exc)) from exc
        except Exception as exc:
            raise CalendarBackendFailure("event_create_failed", str(exc)) from exc

    def get_event(self, *, calendar: str, uid: str) -> dict[str, Any]:
        target = self._calendar(calendar)
        try:
            return event_to_dict(target.event_by_uid(uid))
        except Exception as exc:
            raise CalendarBackendFailure("event_not_found", uid) from exc

    def delete_event(self, *, calendar: str, uid: str) -> dict[str, Any]:
        target = self._calendar(calendar)
        try:
            event = target.event_by_uid(uid)
            before = event_to_dict(event)
            event.delete()
            return before
        except Exception as exc:
            raise CalendarBackendFailure("event_not_found", uid) from exc

    def seed(self, seed: CalendarSeed) -> dict[str, int]:
        calendar_count = 0
        event_count = 0
        for calendar in seed.calendars:
            name = calendar["name"]
            self.create_calendar(name)
            calendar_count += 1
            for event in calendar["events"]:
                self.create_event(calendar=name, **event)
                event_count += 1
        return {"calendar_count": calendar_count, "event_count": event_count}

    def snapshot(self) -> dict[str, Any]:
        calendars: dict[str, Any] = {}
        for item in sorted(self.list_calendars(), key=lambda value: value["name"]):
            name = item["name"]
            events = self.list_events(calendar=name)
            calendars[name] = {
                "event_count": len(events),
                "events": {event["uid"]: event for event in events},
            }
        return {"schema_version": 1, "calendars": calendars}


def seed_radicale(
    *,
    seed: CalendarSeed,
    url: str,
    username: str,
    password: str,
    timeout_seconds: int,
) -> dict[str, int]:
    client = RadicaleCalendarClient(
        url=url,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    try:
        client.delete_all_calendars()
        return client.seed(seed)
    finally:
        client.close()


def snapshot_radicale(
    *,
    url: str,
    username: str,
    password: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    client = RadicaleCalendarClient(
        url=url,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    try:
        return client.snapshot()
    finally:
        client.close()
