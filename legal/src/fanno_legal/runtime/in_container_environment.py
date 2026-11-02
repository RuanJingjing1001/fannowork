from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Callable

from fanno_work.models import (
    TaskSpec,
)

from fanno_legal.backends.calendar import (
    CalendarBackendFailure,
    CalendarSeed,
    parse_calendar_seed,
    seed_radicale,
    snapshot_radicale,
)
from fanno_legal.backends.calendar.direct import (
    DirectRadicaleCalendarBackend,
)
from fanno_legal.backends.email import (
    EmailBackendFailure,
    EmailSeed,
    parse_email_seed,
    seed_greenmail,
    snapshot_greenmail,
)
from fanno_legal.backends.email.direct import (
    DirectGreenMailBackend,
    clear_greenmail,
)
from fanno_legal.capabilities.calendar import (
    CalendarCapability,
)
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
from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)
from fanno_legal.state.audit import (
    AuditLog,
)
from fanno_legal.state.database import (
    StateDatabase,
)
from fanno_legal.state.snapshot import (
    build_environment_snapshot,
)


class InContainerLegalOfficeEnvironment(
    LegalOfficeEnvironment
):
    """
    Legal environment running directly inside the
    Harbor task container.

    LibreOffice and local file backends execute in this
    container. GreenMail and Radicale are external
    Compose services.
    """

    def __init__(self, spec) -> None:
        super().__init__(spec)

        self.email_host: str | None = None
        self.email_imap_port: int | None = None
        self.email_smtp_port: int | None = None
        self.calendar_url: str | None = None

    def prepare(
        self,
        task: TaskSpec,
        run_dir: Path,
    ) -> None:
        if self.task is not None:
            raise RuntimeError(
                "environment is already prepared"
            )

        if task.environment_id != self.spec.id:
            raise ValueError(
                f"task requires {task.environment_id!r}, "
                f"not {self.spec.id!r}"
            )

        resolved_run_dir = Path(
            run_dir
        ).resolve()

        if resolved_run_dir.exists() and any(
            resolved_run_dir.iterdir()
        ):
            raise RuntimeError(
                "run directory must be empty: "
                f"{resolved_run_dir}"
            )

        resolved_run_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        workspace = Path(
            os.environ.get(
                "FANNO_WORKSPACE",
                str(
                    resolved_run_dir
                    / "workspace"
                ),
            )
        ).resolve()

        self._materialize_workspace(
            task,
            workspace,
        )

        database = StateDatabase(
            resolved_run_dir
            / "state"
            / "state.db"
        )
        database.seed(
            task.initial_state
        )

        audit = AuditLog(
            resolved_run_dir
            / "audit"
            / "actions.jsonl"
        )

        self.task = task
        self.run_dir = resolved_run_dir
        self.workspace = workspace
        self.database = database
        self.audit = audit

        self.records = RecordsCapability(
            database,
            audit,
        )

        self.filesystem = FilesystemCapability(
            workspace=workspace,
            database=database,
            audit=audit,
            config=self.filesystem_config,
        )

        self.document = DocumentCapability(
            filesystem=self.filesystem,
            database=database,
            audit=audit,
            config=self.document_config,
        )

        self.writer = WriterCapability(
            filesystem=self.filesystem,
            database=database,
            audit=audit,
            config=self.document_config,
        )

        self.spreadsheet = (
            SpreadsheetCapability(
                filesystem=self.filesystem,
                database=database,
                audit=audit,
                config=(
                    self.spreadsheet_config
                ),
            )
        )

        self._prepare_email(
            task,
            database,
            audit,
        )

        self._prepare_calendar(
            task,
            database,
            audit,
        )

        self._apply_workspace_permissions()

    def _prepare_email(
        self,
        task: TaskSpec,
        database: StateDatabase,
        audit: AuditLog,
    ) -> None:
        enabled = bool(
            EmailCapability.ACTION_NAMES
            & self._enabled_action_set
        )

        if not enabled:
            return

        seed = parse_email_seed(
            task.initial_state
        )

        service_config = (
            self.email_config.get(
                "service",
                {},
            )
        )

        host = os.environ.get(
            "FANNO_GREENMAIL_HOST",
            str(
                service_config.get(
                    "host",
                    "greenmail",
                )
            ),
        )

        imap_port = int(
            os.environ.get(
                "FANNO_GREENMAIL_IMAP_PORT",
                str(
                    service_config.get(
                        "imap_port",
                        3143,
                    )
                ),
            )
        )

        smtp_port = int(
            os.environ.get(
                "FANNO_GREENMAIL_SMTP_PORT",
                str(
                    service_config.get(
                        "smtp_port",
                        3025,
                    )
                ),
            )
        )

        timeout_seconds = int(
            self.email_config.get(
                "timeout_seconds",
                15,
            )
        )

        self._retry(
            lambda: seed_greenmail(
                seed=seed,
                host=host,
                imap_port=imap_port,
                smtp_port=smtp_port,
                timeout_seconds=(
                    timeout_seconds
                ),
            ),
            retryable=(
                EmailBackendFailure,
            ),
            label="GreenMail",
        )

        account_id = (
            self.email_config.get(
                "agent_account"
            )
        )

        if (
            not isinstance(account_id, str)
            or account_id
            not in seed.accounts
        ):
            raise ValueError(
                "email.agent_account does not "
                "exist in task initial state"
            )

        backend = DirectGreenMailBackend(
            workspace=self.workspace,
            account=seed.accounts[
                account_id
            ],
            host=host,
            imap_port=imap_port,
            smtp_port=smtp_port,
            timeout_seconds=timeout_seconds,
        )

        self.email = EmailCapability(
            backend=backend,
            filesystem=self.filesystem,
            database=database,
            audit=audit,
            config=self.email_config,
        )

        self.email_seed = seed
        self.email_host = host
        self.email_imap_port = imap_port
        self.email_smtp_port = smtp_port

    def _prepare_calendar(
        self,
        task: TaskSpec,
        database: StateDatabase,
        audit: AuditLog,
    ) -> None:
        enabled = bool(
            CalendarCapability.ACTION_NAMES
            & self._enabled_action_set
        )

        if not enabled:
            return

        seed = parse_calendar_seed(
            task.initial_state
        )

        service_config = (
            self.calendar_config.get(
                "service",
                {},
            )
        )

        url = os.environ.get(
            "FANNO_RADICALE_URL",
            str(
                service_config.get(
                    "url",
                    "http://radicale:5232",
                )
            ),
        )

        username = str(
            self.calendar_config.get(
                "username",
                "benchmark",
            )
        )
        password = str(
            self.calendar_config.get(
                "password",
                "",
            )
        )
        timeout_seconds = int(
            self.calendar_config.get(
                "timeout_seconds",
                15,
            )
        )

        self._retry(
            lambda: seed_radicale(
                seed=seed,
                url=url,
                username=username,
                password=password,
                timeout_seconds=(
                    timeout_seconds
                ),
            ),
            retryable=(
                CalendarBackendFailure,
            ),
            label="Radicale",
        )

        backend = (
            DirectRadicaleCalendarBackend(
                url=url,
                username=username,
                password=password,
                timeout_seconds=(
                    timeout_seconds
                ),
            )
        )

        self.calendar = CalendarCapability(
            backend=backend,
            database=database,
            audit=audit,
            config=self.calendar_config,
        )

        self.calendar_seed = seed
        self.calendar_url = url

    @staticmethod
    def _retry(
        operation: Callable[[], Any],
        *,
        retryable: tuple[type[Exception], ...],
        label: str,
        attempts: int = 20,
    ) -> Any:
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                return operation()
            except retryable as exc:
                last_error = exc

                if attempt + 1 >= attempts:
                    break

                time.sleep(
                    min(
                        0.25
                        * (attempt + 1),
                        2.0,
                    )
                )

        raise RuntimeError(
            f"{label} did not become ready"
        ) from last_error

    def snapshot(self) -> dict:
        (
            _,
            database,
            _,
            _,
            _,
            _,
            _,
            workspace,
        ) = self._prepared_components()

        mailboxes = None
        calendars = None

        if (
            isinstance(
                self.email_seed,
                EmailSeed,
            )
            and self.email_host is not None
            and self.email_imap_port
            is not None
        ):
            mailboxes = snapshot_greenmail(
                seed=self.email_seed,
                host=self.email_host,
                imap_port=(
                    self.email_imap_port
                ),
                timeout_seconds=int(
                    self.email_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        if (
            isinstance(
                self.calendar_seed,
                CalendarSeed,
            )
            and self.calendar_url is not None
        ):
            calendars = snapshot_radicale(
                url=self.calendar_url,
                username=str(
                    self.calendar_config.get(
                        "username",
                        "benchmark",
                    )
                ),
                password=str(
                    self.calendar_config.get(
                        "password",
                        "",
                    )
                ),
                timeout_seconds=int(
                    self.calendar_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        return build_environment_snapshot(
            database,
            workspace,
            mailboxes=mailboxes,
            calendars=calendars,
        )

    def reset(self) -> None:
        (
            task,
            database,
            audit,
            _,
            _,
            _,
            _,
            workspace,
        ) = self._prepared_components()

        version_before = (
            database.get_state_version()
        )

        database.reset()

        self._materialize_workspace(
            task,
            workspace,
        )

        if (
            isinstance(
                self.email_seed,
                EmailSeed,
            )
            and self.email_host is not None
            and self.email_imap_port
            is not None
            and self.email_smtp_port
            is not None
        ):
            clear_greenmail(
                seed=self.email_seed,
                host=self.email_host,
                imap_port=(
                    self.email_imap_port
                ),
                timeout_seconds=int(
                    self.email_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

            seed_greenmail(
                seed=self.email_seed,
                host=self.email_host,
                imap_port=(
                    self.email_imap_port
                ),
                smtp_port=(
                    self.email_smtp_port
                ),
                timeout_seconds=int(
                    self.email_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        if (
            isinstance(
                self.calendar_seed,
                CalendarSeed,
            )
            and self.calendar_url is not None
        ):
            seed_radicale(
                seed=self.calendar_seed,
                url=self.calendar_url,
                username=str(
                    self.calendar_config.get(
                        "username",
                        "benchmark",
                    )
                ),
                password=str(
                    self.calendar_config.get(
                        "password",
                        "",
                    )
                ),
                timeout_seconds=int(
                    self.calendar_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        self._apply_workspace_permissions()

        version_after = (
            database.get_state_version()
        )

        audit.append(
            actor="environment",
            action="environment.reset",
            arguments={},
            ok=True,
            error=None,
            state_version_before=(
                version_before
            ),
            state_version_after=(
                version_after
            ),
        )

    def close(self) -> None:
        return None

    def _apply_workspace_permissions(
        self,
    ) -> None:
        if self.workspace is None:
            return

        raw_gid = os.environ.get(
            "FANNO_AGENT_GID"
        )

        gid = (
            int(raw_gid)
            if raw_gid is not None
            else None
        )

        inputs = self.workspace / "inputs"
        work = self.workspace / "work"
        outputs = self.workspace / "outputs"

        for path in inputs.rglob("*"):
            if path.is_symlink():
                continue

            if path.is_dir():
                path.chmod(0o555)
            elif path.is_file():
                path.chmod(0o444)

        inputs.chmod(0o555)

        for root in (work, outputs):
            root.mkdir(
                parents=True,
                exist_ok=True,
            )
            root.chmod(0o2770)

            if gid is not None:
                os.chown(root, -1, gid)

            for path in root.rglob("*"):
                if path.is_symlink():
                    continue

                if path.is_dir():
                    path.chmod(0o2770)
                elif path.is_file():
                    path.chmod(0o660)

                if gid is not None:
                    os.chown(path, -1, gid)