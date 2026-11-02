from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import shutil

from fanno_work.environments.base import (
    WorkflowEnvironment,
)
from fanno_work.models import (
    ActionRequest,
    ActionResult,
    EnvironmentSpec,
    Observation,
    TaskSpec,
)
from fanno_work.sandbox import ContainerWorkspace

from fanno_legal.backends.document.container_libreoffice import (
    ContainerLibreOfficeBackend,
)
from fanno_legal.backends.document.container_document import (
    ContainerLibreOfficeConversionBackend,
    ContainerTextExtractor,
)
from fanno_legal.backends.filesystem import (
    ContainerFilesystemBackend,
)
from fanno_legal.backends.spreadsheet.container_libreoffice import (
    ContainerLibreOfficeCalcBackend,
)

from fanno_legal.backends.email import (
    ContainerGreenMailBackend,
    GreenMailClient,
    GreenMailService,
    parse_email_seed,
    seed_greenmail,
)

from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.capabilities.document import (
    DocumentCapability,
)
from fanno_legal.capabilities.records import (
    RecordsCapability,
)
from fanno_legal.contracts import (
    load_action_contracts,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)
from fanno_legal.state.snapshot import (
    build_environment_snapshot,
)

from fanno_legal.capabilities.spreadsheet import (
    SpreadsheetCapability,
)


from fanno_legal.capabilities.email import (
    EmailCapability,
)

from fanno_legal.capabilities.writer import (
    WriterCapability,
)

from fanno_legal.backends.email import (
    ContainerGreenMailBackend,
    GreenMailService,
    parse_email_seed,
    seed_greenmail,
    snapshot_greenmail,
)
from fanno_legal.backends.calendar import (
    CalendarSeed,
    ContainerRadicaleCalendarBackend,
    RadicaleService,
    parse_calendar_seed,
    seed_radicale,
    snapshot_radicale,
)
from fanno_legal.capabilities.calendar import (
    CalendarCapability,
)

class LegalOfficeEnvironment(
    WorkflowEnvironment
):
    """Local legal-office runtime for one task instance."""

    def __init__(
        self,
        spec: EnvironmentSpec,
    ) -> None:
        if spec.adapter != "legal_office":
            raise ValueError(
                "unsupported environment adapter: "
                f"{spec.adapter}"
            )

        self.spec = spec
        self.contracts = load_action_contracts(
            spec
        )
        self._contracts_by_id = {
            contract.id: contract
            for contract in self.contracts
        }

        implemented_actions = (
            RecordsCapability.ACTION_NAMES
            | FilesystemCapability.ACTION_NAMES
            | DocumentCapability.ACTION_NAMES
            | SpreadsheetCapability.ACTION_NAMES
            | WriterCapability.ACTION_NAMES
            | EmailCapability.ACTION_NAMES
            | CalendarCapability.ACTION_NAMES
        )

        raw_enabled_actions = spec.config.get(
            "enabled_actions"
        )

        if not isinstance(
            raw_enabled_actions,
            list,
        ) or not all(
            isinstance(action, str) and action
            for action in raw_enabled_actions
        ):
            raise ValueError(
                "environment config.enabled_actions "
                "must be a non-empty string list"
            )

        if not raw_enabled_actions:
            raise ValueError(
                "environment config.enabled_actions "
                "must not be empty"
            )

        if len(raw_enabled_actions) != len(
            set(raw_enabled_actions)
        ):
            raise ValueError(
                "environment config.enabled_actions "
                "contains duplicates"
            )

        undeclared = set(
            raw_enabled_actions
        ) - set(self._contracts_by_id)

        if undeclared:
            raise ValueError(
                "enabled actions are not declared "
                "in the catalog: "
                f"{sorted(undeclared)}"
            )

        unavailable = set(
            raw_enabled_actions
        ) - implemented_actions

        if unavailable:
            raise ValueError(
                "enabled actions have no runtime "
                "implementation: "
                f"{sorted(unavailable)}"
            )

        filesystem_config = spec.config.get(
            "filesystem"
        )

        if not isinstance(
            filesystem_config,
            dict,
        ):
            raise ValueError(
                "environment config.filesystem "
                "must be an object"
            )

        document_config = spec.config.get(
            "document"
        )
        if not isinstance(document_config, dict):
            raise ValueError(
                "environment config.document "
                "must be an object"
            )

        spreadsheet_config = spec.config.get(
            "spreadsheet"
        )
        if not isinstance(spreadsheet_config, dict):
            raise ValueError(
                "environment config.spreadsheet "
                "must be an object"
            )
        
        email_config = spec.config.get(
            "email",
            {},
        )

        if not isinstance(email_config, dict):
            raise ValueError(
                "environment config.email "
                "must be an object"
            )

        calendar_config = spec.config.get(
            "calendar",
            {},
        )
        if not isinstance(calendar_config, dict):
            raise ValueError(
                "environment config.calendar must be an object"
            )
        
        execution_config = spec.config.get(
            "execution",
            {"backend": "local"},
        )
        if not isinstance(execution_config, dict):
            raise ValueError(
                "environment config.execution "
                "must be an object"
            )
        execution_backend = execution_config.get(
            "backend",
            "local",
        )
        if execution_backend not in {
            "local",
            "container",
            "in_container",
        }:
            raise ValueError(
                "environment config.execution.backend "
                "must be 'local' or 'container' "
                "or 'in_container'"
            )
        container_config = execution_config.get(
            "container",
            {},
        )
        if not isinstance(container_config, dict):
            raise ValueError(
                "environment config.execution.container "
                "must be an object"
            )

        self.enabled_actions = tuple(
            raw_enabled_actions
        )
        self._enabled_action_set = frozenset(
            raw_enabled_actions
        )
        self.filesystem_config = dict(
            filesystem_config
        )
        self.document_config = dict(
            document_config
        )
        self.spreadsheet_config = dict(
            spreadsheet_config
        )
        self.email_config = dict(email_config)
        self.calendar_config = dict(calendar_config)
        self.execution_backend = execution_backend
        self.container_config = dict(
            container_config
        )
        self.task: TaskSpec | None = None
        self.run_dir: Path | None = None
        self.workspace: Path | None = None
        self.database: StateDatabase | None = None
        self.audit: AuditLog | None = None
        self.records: RecordsCapability | None = None
        self.filesystem: (
            FilesystemCapability | None
        ) = None
        self.document: DocumentCapability | None = None
        self.spreadsheet: SpreadsheetCapability | None = None
        self.email: EmailCapability | None = None
        self.greenmail_service: (
            GreenMailService | None
        ) = None
        self.email_seed = None
        self.calendar: CalendarCapability | None = None
        self.radicale_service: RadicaleService | None = None
        self.calendar_seed: CalendarSeed | None = None
        self.writer: WriterCapability | None = None
        self.container_workspace: (
            ContainerWorkspace | None
        ) = None

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
                f"task requires "
                f"{task.environment_id!r}, "
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

        workspace = (
            resolved_run_dir / "workspace"
        )
        self._materialize_workspace(
            task,
            workspace,
        )

        database = StateDatabase(
            resolved_run_dir
            / "state"
            / "state.db"
        )
        database.seed(task.initial_state)

        audit = AuditLog(
            resolved_run_dir
            / "audit"
            / "actions.jsonl"
        )
        email_enabled = bool(
            EmailCapability.ACTION_NAMES
            & self._enabled_action_set
        )
        calendar_enabled = bool(
            CalendarCapability.ACTION_NAMES
            & self._enabled_action_set
        )

        self.task = task
        self.run_dir = resolved_run_dir
        self.workspace = workspace
        self.database = database
        self.audit = audit

        filesystem_backend = None
        writer_backend = None
        spreadsheet_backend = None
        email_backend = None
        calendar_backend = None
        document_uno_backend = None
        document_conversion_backend = None
        document_text_extractor = None


        email_enabled = bool(
            EmailCapability.ACTION_NAMES
            & self._enabled_action_set
        )


        if email_enabled:
            if self.execution_backend != "container":
                raise ValueError(
                    "email actions require "
                    "container execution"
                )

            email_seed = parse_email_seed(
                task.initial_state
            )

            service_config = (
                self.email_config.get(
                    "service",
                    {},
                )
            )

            if not isinstance(
                service_config,
                dict,
            ):
                raise ValueError(
                    "email.service must be an object"
                )

            greenmail_service = GreenMailService(
                image=str(
                    service_config.get(
                        "image",
                        "greenmail/standalone:2.1.8",
                    )
                ),
                docker_executable=str(
                    self.container_config.get(
                        "docker_executable",
                        "docker",
                    )
                ),
                startup_timeout_seconds=int(
                    service_config.get(
                        "startup_timeout_seconds",
                        60,
                    )
                ),
            )

            greenmail_service.start()

            self.greenmail_service = (
                greenmail_service
            )
            self.email_seed = email_seed

            seed_greenmail(
                seed=email_seed,
                host="127.0.0.1",
                imap_port=(
                    greenmail_service
                    .imap_host_port
                ),
                smtp_port=(
                    greenmail_service
                    .smtp_host_port
                ),
                timeout_seconds=int(
                    self.email_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        if calendar_enabled:
            if self.execution_backend != "container":
                raise ValueError(
                    "calendar actions require container execution"
                )

            calendar_seed = parse_calendar_seed(
                task.initial_state
            )
            calendar_service_config = (
                self.calendar_config.get(
                    "service",
                    {},
                )
            )
            if not isinstance(
                calendar_service_config,
                dict,
            ):
                raise ValueError(
                    "calendar.service must be an object"
                )

            radicale_service = RadicaleService(
                image=str(
                    calendar_service_config.get(
                        "image",
                        (
                            "tomsquest/docker-radicale@sha256:"
                            "6a86c8fd17ff4a10b53487af8d635b68"
                            "e7b8b38c8770320de56c9d44f45bbd73"
                        ),
                    )
                ),
                docker_executable=str(
                    self.container_config.get(
                        "docker_executable",
                        "docker",
                    )
                ),
                startup_timeout_seconds=int(
                    calendar_service_config.get(
                        "startup_timeout_seconds",
                        60,
                    )
                ),
                network_name=(
                    self.greenmail_service.network_name
                    if self.greenmail_service is not None
                    else None
                ),
            )
            radicale_service.start()
            self.radicale_service = radicale_service
            self.calendar_seed = calendar_seed

            seed_radicale(
                seed=calendar_seed,
                url=radicale_service.host_url,
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

        if self.execution_backend == "container":
            image = self.container_config.get(
                "image",
                self.spec.config.get(
                    "runtime_image"
                ),
            )
            if not isinstance(image, str) or not image:
                raise ValueError(
                    "container execution requires "
                    "a non-empty image"
                )

            container_workspace = (
                ContainerWorkspace(
                    workspace=workspace,
                    image=image,
                    docker_executable=str(
                        self.container_config.get(
                            "docker_executable",
                            "docker",
                        )
                    ),
                    network=(
                        self.greenmail_service.network_name
                        if self.greenmail_service
                        is not None
                        else (
                            self.radicale_service.network_name
                            if self.radicale_service is not None
                            else str(
                                self.container_config.get(
                                    "network",
                                    "none",
                                )
                            )
                        )
                    ),
                    cpu_limit=(
                        self.container_config.get(
                            "cpu_limit",
                            2.0,
                        )
                    ),
                    memory_limit=(
                        self.container_config.get(
                            "memory_limit",
                            "2g",
                        )
                    ),
                    pids_limit=(
                        self.container_config.get(
                            "pids_limit",
                            256,
                        )
                    ),
                    default_timeout_seconds=int(
                        self.container_config.get(
                            "default_timeout_seconds",
                            60,
                        )
                    ),
                )
            )
            container_workspace.start()
            self.container_workspace = (
                container_workspace
            )

            if email_enabled:
                account_id = (
                    self.email_config.get(
                        "agent_account"
                    )
                )
                if (
                    not isinstance(
                        account_id,
                        str,
                    )
                    or self.email_seed is None
                    or account_id
                    not in self.email_seed.accounts
                ):
                    raise ValueError(
                        "email.agent_account does not "
                        "exist in task initial state"
                    )

                email_backend = (
                    ContainerGreenMailBackend(
                        workspace=(
                            container_workspace
                        ),
                        account=(
                            self.email_seed.accounts[
                                account_id
                            ]
                        ),
                        host=str(
                            service_config.get(
                                "host",
                                "greenmail",
                            )
                        ),
                        imap_port=int(
                            service_config.get(
                                "imap_port",
                                3143,
                            )
                        ),
                        smtp_port=int(
                            service_config.get(
                                "smtp_port",
                                3025,
                            )
                        ),
                        timeout_seconds=int(
                            self.email_config.get(
                                "timeout_seconds",
                                15,
                            )
                        ),
                        attachment_writable_prefixes=(
                            tuple(
                                self.email_config.get(
                                    "attachment_writable_prefixes",
                                    [
                                        "work/"
                                        "email-attachments/"
                                    ],
                                )
                            )
                        ),
                    )
                )

            if calendar_enabled:
                calendar_service_config = (
                    self.calendar_config.get(
                        "service",
                        {},
                    )
                )
                calendar_backend = (
                    ContainerRadicaleCalendarBackend(
                        workspace=container_workspace,
                        url=str(
                            calendar_service_config.get(
                                "url",
                                "http://radicale:5232",
                            )
                        ),
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
                )

            filesystem_backend = (
                ContainerFilesystemBackend(
                    container_workspace
                )
            )

            uno_config = self.document_config[
                "uno"
            ]
            writer_backend = (
                ContainerLibreOfficeBackend(
                    workspace=container_workspace,
                    system_python=str(
                        uno_config.get(
                            "system_python",
                            "/usr/bin/python3",
                        )
                    ),
                    soffice_executable=str(
                        uno_config.get(
                            "soffice_executable",
                            "soffice",
                        )
                    ),
                    timeout_seconds=int(
                        uno_config.get(
                            "timeout_seconds",
                            60,
                        )
                    ),
                )
            )
            document_uno_backend = writer_backend

            conversion_config = self.document_config[
                "conversion"
            ]
            document_conversion_backend = (
                ContainerLibreOfficeConversionBackend(
                    workspace=container_workspace,
                    executable=str(
                        conversion_config.get(
                            "executable",
                            "soffice",
                        )
                    ),
                    timeout_seconds=int(
                        conversion_config.get(
                            "timeout_seconds",
                            60,
                        )
                    ),
                )
            )

            extraction_config = self.document_config.get(
                "extraction",
                {},
            )
            if not isinstance(extraction_config, dict):
                raise ValueError(
                    "document.extraction must be an object"
                )
            document_text_extractor = ContainerTextExtractor(
                workspace=container_workspace,
                max_archive_uncompressed_bytes=int(
                    self.document_config[
                        "max_archive_uncompressed_bytes"
                    ]
                ),
                timeout_seconds=int(
                    extraction_config.get(
                        "timeout_seconds",
                        self.container_config.get(
                            "default_timeout_seconds",
                            60,
                        ),
                    )
                ),
            )

            calc_config = self.spreadsheet_config[
                "uno"
            ]
            spreadsheet_backend = (
                ContainerLibreOfficeCalcBackend(
                    workspace=container_workspace,
                    system_python=str(
                        calc_config.get(
                            "system_python",
                            "/usr/bin/python3",
                        )
                    ),
                    soffice_executable=str(
                        calc_config.get(
                            "soffice_executable",
                            "soffice",
                        )
                    ),
                    timeout_seconds=int(
                        calc_config.get(
                            "timeout_seconds",
                            60,
                        )
                    ),
                )
            )

        self.records = RecordsCapability(
            database,
            audit,
        )
        self.filesystem = FilesystemCapability(
            workspace=workspace,
            database=database,
            audit=audit,
            config=self.filesystem_config,
            backend=filesystem_backend,
        )
        self.document = DocumentCapability(
            filesystem=self.filesystem,
            database=database,
            audit=audit,
            config=self.document_config,
            libreoffice_backend=(
                document_conversion_backend
            ),
            uno_backend=document_uno_backend,
            text_extractor=document_text_extractor,
        )
        self.writer = WriterCapability(
            filesystem=self.filesystem,
            database=database,
            audit=audit,
            config=self.document_config,
            backend=writer_backend,
        )

        self.spreadsheet = SpreadsheetCapability(
            filesystem=self.filesystem,
            database=database,
            audit=audit,
            config=self.spreadsheet_config,
            backend=spreadsheet_backend,
        )
        if email_backend is not None:
            self.email = EmailCapability(
                backend=email_backend,
                filesystem=self.filesystem,
                database=database,
                audit=audit,
                config=self.email_config,
            )
        if calendar_backend is not None:
            self.calendar = CalendarCapability(
                backend=calendar_backend,
                database=database,
                audit=audit,
                config=self.calendar_config,
            )

    def observe(self) -> Observation:
        (
            task,
            database,
            _,
            _,
            _,
            _,
            _,
            workspace,
        ) = self._prepared_components()

        descriptors = tuple(
            self._contracts_by_id[
                action
            ].to_descriptor()
            for action in self.enabled_actions
        )

        return Observation(
            task_id=task.id,
            state_version=(
                database.get_state_version()
            ),
            available_actions=descriptors,
            data={
                "instruction": task.instruction,
            },
        )

    def act(
        self,
        request: ActionRequest,
    ) -> ActionResult:
        (
            _,
            database,
            audit,
            records,
            filesystem,
            document,
            spreadsheet,
            _,
        ) = self._prepared_components()

        if request.name not in (
            self._enabled_action_set
        ):
            return self._reject_action(
                request=request,
                database=database,
                audit=audit,
            )

        if request.name in (
            RecordsCapability.ACTION_NAMES
        ):
            return records.act(
                "agent",
                request,
            )

        if request.name in (
            FilesystemCapability.ACTION_NAMES
        ):
            return filesystem.act(
                "agent",
                request,
            )

        if request.name in DocumentCapability.ACTION_NAMES:
            return document.act(
                "agent",
                request,
            )
        if request.name in WriterCapability.ACTION_NAMES:
            if self.writer is None:
                raise RuntimeError(
                    "writer capability is not prepared"
                )

            return self.writer.act(
                "agent",
                request,
            )


        if request.name in SpreadsheetCapability.ACTION_NAMES:
            return spreadsheet.act(
                "agent",
                request,
            )

        if request.name in EmailCapability.ACTION_NAMES:
            if self.email is None:
                raise RuntimeError(
                    "email capability is not prepared"
                )

            return self.email.act(
                "agent",
                request,
            )

        if request.name in CalendarCapability.ACTION_NAMES:
            if self.calendar is None:
                raise RuntimeError(
                    "calendar capability is not prepared"
                )
            return self.calendar.act(
                "agent",
                request,
            )

        return self._reject_action(
            request=request,
            database=database,
            audit=audit,
        )

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
            self.greenmail_service is not None
            and self.email_seed is not None
        ):
            imap_port = (
                self.greenmail_service
                .imap_host_port
            )

            if imap_port is None:
                raise RuntimeError(
                    "GreenMail IMAP port "
                    "is unavailable"
                )

            mailboxes = snapshot_greenmail(
                seed=self.email_seed,
                host="127.0.0.1",
                imap_port=imap_port,
                timeout_seconds=int(
                    self.email_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        if self.radicale_service is not None:
            calendars = snapshot_radicale(
                url=self.radicale_service.host_url,
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
        if self.container_workspace is not None:
            self.container_workspace.stop()

        self._materialize_workspace(
            task,
            workspace,
        )

        if self.greenmail_service is not None:
            self.greenmail_service.restart()

            seed_greenmail(
                seed=self.email_seed,
                host="127.0.0.1",
                imap_port=(
                self.greenmail_service
                .imap_host_port
                ),
                smtp_port=(
                self.greenmail_service
                .smtp_host_port
                ),
                timeout_seconds=int(
                    self.email_config.get(
                        "timeout_seconds",
                        15,
                    )
                ),
            )

        if self.radicale_service is not None:
            self.radicale_service.restart()
            if self.calendar_seed is None:
                raise RuntimeError(
                    "calendar seed is unavailable"
                )
            seed_radicale(
                seed=self.calendar_seed,
                url=self.radicale_service.host_url,
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

        if self.container_workspace is not None:
            self.container_workspace.start()

        version_after = (
            database.get_state_version()
        )

        audit.append(
            actor="environment",
            action="environment.reset",
            arguments={},
            ok=True,
            error=None,
            state_version_before=version_before,
            state_version_after=version_after,
        )

    def close(self) -> None:
        if self.container_workspace is not None:
            self.container_workspace.stop()

        if self.radicale_service is not None:
            self.radicale_service.stop()

        if self.greenmail_service is not None:
            self.greenmail_service.stop()

    def _prepared_components(
        self,
    ) -> tuple[
        TaskSpec,
        StateDatabase,
        AuditLog,
        RecordsCapability,
        FilesystemCapability,
        DocumentCapability,
        SpreadsheetCapability,
        Path,
    ]:
        if (
            self.task is None
            or self.database is None
            or self.audit is None
            or self.records is None
            or self.filesystem is None
            or self.document is None
            or self.spreadsheet is None
            or self.workspace is None
        ):
            raise RuntimeError(
                "environment is not prepared"
            )

        return (
            self.task,
            self.database,
            self.audit,
            self.records,
            self.filesystem,
            self.document,
            self.spreadsheet,
            self.workspace,
        )

    @staticmethod
    def _reject_action(
        *,
        request: ActionRequest,
        database: StateDatabase,
        audit: AuditLog,
    ) -> ActionResult:
        version = (
            database.get_state_version()
        )

        result = ActionResult(
            ok=False,
            error="unknown_action",
        )

        audit.append(
            actor="agent",
            action=request.name,
            arguments=request.arguments,
            ok=False,
            error=result.error,
            state_version_before=version,
            state_version_after=version,
        )

        return result

    @staticmethod
    def _materialize_workspace(
        task: TaskSpec,
        workspace: Path,
    ) -> None:
        workspace = Path(workspace).resolve()

        if workspace.exists():
            shutil.rmtree(workspace)

        inputs_root = workspace / "inputs"
        work_root = workspace / "work"
        outputs_root = workspace / "outputs"

        inputs_root.mkdir(
            parents=True,
            exist_ok=True,
        )
        work_root.mkdir()
        outputs_root.mkdir()

        source_root = (
            task.source_dir.resolve()
        )

        for artifact in task.inputs:
            if (
                not artifact.path
                or "\\" in artifact.path
            ):
                raise ValueError(
                    "invalid input artifact path: "
                    f"{artifact.path!r}"
                )

            relative = PurePosixPath(
                artifact.path
            )

            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() == "."
            ):
                raise ValueError(
                    "invalid input artifact path: "
                    f"{artifact.path!r}"
                )

            source = (
                source_root
                / artifact.source
            ).resolve()

            if not source.is_relative_to(
                source_root
            ):
                raise ValueError(
                    "input source escapes task "
                    "directory: "
                    f"{artifact.source}"
                )

            if not source.is_file():
                raise ValueError(
                    "input source does not exist: "
                    f"{artifact.source}"
                )

            if artifact.sha256:
                digest = hashlib.sha256(
                    source.read_bytes()
                ).hexdigest()

                if digest != artifact.sha256:
                    raise ValueError(
                        "input sha256 mismatch: "
                        f"{artifact.source}"
                    )

            target = inputs_root.joinpath(
                *relative.parts
            ).resolve()

            if not target.is_relative_to(
                inputs_root.resolve()
            ):
                raise ValueError(
                    "input target escapes workspace: "
                    f"{artifact.path}"
                )

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            shutil.copy2(
                source,
                target,
            )
