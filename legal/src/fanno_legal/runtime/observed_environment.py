from __future__ import annotations

import os
from pathlib import Path
import time

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
from fanno_work.observability import (
    TraceSession,
    snapshot_diff,
    summarize_action_result,
)

from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)
from fanno_legal.capabilities.writer import (
    WriterCapability,
)

from fanno_legal.runtime.in_container_environment import (
    InContainerLegalOfficeEnvironment,
)

_CAPABILITY_COMPONENTS = {
    "filesystem": "FilesystemCapability",
    "document": "DocumentCapability",
    "spreadsheet": "SpreadsheetCapability",
    "email": "EmailCapability",
    "calendar": "CalendarCapability",
    "records": "RecordsCapability",
}


class ObservedLegalOfficeEnvironment(
    WorkflowEnvironment
):
    """
    Observability wrapper.

    It records execution evidence without changing the
    behavior of LegalOfficeEnvironment.
    """

    def __init__(
        self,
        spec: EnvironmentSpec,
    ) -> None:
        self.spec = spec
        execution = spec.config.get(
            "execution",
            {},
        )

        backend = (
            execution.get("backend")
            if isinstance(execution, dict)
            else None
        )

        if backend == "in_container":
            factory = (
                InContainerLegalOfficeEnvironment
            )
        else:
            factory = LegalOfficeEnvironment

        self.inner = factory(spec)
        self.trace: TraceSession | None = None

    def prepare(
        self,
        task: TaskSpec,
        run_dir: Path,
    ) -> None:
        started = time.monotonic()

        self.inner.prepare(
            task,
            run_dir,
        )

        console = (
            os.environ.get(
                "FANNO_TRACE_CONSOLE",
                "0",
            )
            == "1"
        )

        self.trace = TraceSession(
            run_dir,
            run_id=Path(run_dir).name,
            console=console,
        )

        duration = (
            time.monotonic()
            - started
        ) * 1000

        self.trace.emit(
            "environment.prepared",
            layer="environment",
            component=(
                "ObservedLegalOfficeEnvironment"
            ),
            operation="prepare",
            status="success",
            duration_ms=duration,
            details={
                "task_id": task.id,
                "environment_id": (
                    task.environment_id
                ),
                "input_count": len(
                    task.inputs
                ),
                "output_count": len(
                    task.outputs
                ),
                "workspace": "workspace/",
            },
        )

    def observe(self) -> Observation:
        observation = self.inner.observe()

        if self.trace is not None:
            self.trace.emit(
                "observation.delivered",
                layer="agent_interface",
                component="environment",
                operation="observe",
                status="success",
                details={
                    "task_id": (
                        observation.task_id
                    ),
                    "state_version": (
                        observation
                        .state_version
                    ),
                    "available_actions": [
                        item.name
                        for item
                        in observation
                        .available_actions
                    ],
                    "data_keys": sorted(
                        observation.data
                    ),
                },
                binding=(
                    self.trace.last_binding
                ),
                visibility="agent_visible",
            )

        return observation

    def act(
        self,
        request: ActionRequest,
    ) -> ActionResult:
        if self.trace is None:
            raise RuntimeError(
                "observed environment "
                "is not prepared"
            )

        before = self.inner.snapshot()
        started = time.monotonic()

        prefix = request.name.split(
            ".",
            1,
        )[0]

        if request.name in WriterCapability.ACTION_NAMES:
            component = "WriterCapability"
        else:
            component = (
                _CAPABILITY_COMPONENTS.get(
                    prefix,
                    "UnknownCapability",
                )
            )

        with self.trace.action_context(
            request.name,
            request.arguments,
        ) as binding:
            try:
                with self.trace.span(
                    layer="capability",
                    component=component,
                    operation=request.name,
                    details={
                        "action": (
                            request.name
                        )
                    },
                ) as span:
                    result = self.inner.act(
                        request
                    )

                    summary = (
                        summarize_action_result(
                            result
                        )
                    )

                    span.set_output_summary(
                        **summary
                    )

                    backend = (
                        result.output.get(
                            "backend"
                        )
                        if isinstance(
                            result.output,
                            dict,
                        )
                        else None
                    )

                    if isinstance(
                        backend,
                        str,
                    ):
                        self.trace.emit(
                            "backend.reported",
                            layer="backend",
                            component=backend,
                            operation=(
                                request.name
                            ),
                            status=(
                                "success"
                                if result.ok
                                else "failed"
                            ),
                            details={
                                "backend": backend,
                                "backend_version": (
                                    result.output
                                    .get(
                                        "backend_version"
                                    )
                                ),
                                "source_sha256": (
                                    result.output
                                    .get(
                                        "source_sha256"
                                    )
                                ),
                                "execution_evidence": (
                                    result.output
                                    .get(
                                        "execution_evidence",
                                        {},
                                    )
                                ),
                                "note": (
                                    "Backend identity "
                                    "reported by the "
                                    "parsed worker result."
                                ),
                            },
                            binding=binding,
                        )

                after = self.inner.snapshot()
                diff = snapshot_diff(
                    before,
                    after,
                )

                self.trace.emit(
                    "state.diff",
                    layer="state",
                    component=(
                        "EnvironmentSnapshot"
                    ),
                    operation=request.name,
                    status="success",
                    details=diff,
                    binding=binding,
                    visibility=(
                        "evaluator_only"
                    ),
                )

                duration = (
                    time.monotonic()
                    - started
                ) * 1000

                self.trace.emit(
                    "action.result",
                    layer="agent_interface",
                    component="environment",
                    operation=request.name,
                    status=(
                        "success"
                        if result.ok
                        else "failed"
                    ),
                    details={
                        "result": (
                            summarize_action_result(
                                result
                            )
                        )
                    },
                    error=(
                        {
                            "code": (
                                result.error
                            )
                        }
                        if result.error
                        else None
                    ),
                    duration_ms=duration,
                    binding=binding,
                    visibility=(
                        "agent_visible"
                    ),
                )

                return result

            except Exception as exc:
                after = self.inner.snapshot()
                diff = snapshot_diff(
                    before,
                    after,
                )

                self.trace.emit(
                    "state.diff",
                    layer="state",
                    component=(
                        "EnvironmentSnapshot"
                    ),
                    operation=request.name,
                    status="failed",
                    details=diff,
                    binding=binding,
                    visibility=(
                        "evaluator_only"
                    ),
                )

                duration = (
                    time.monotonic()
                    - started
                ) * 1000

                self.trace.emit(
                    "action.result",
                    layer="agent_interface",
                    component="environment",
                    operation=request.name,
                    status="failed",
                    error={
                        "type": (
                            type(exc).__name__
                        ),
                        "message": str(exc),
                    },
                    duration_ms=duration,
                    binding=binding,
                    visibility=(
                        "agent_visible"
                    ),
                )
                raise

    def snapshot(self) -> dict:
        return self.inner.snapshot()

    def reset(self) -> None:
        before = self.inner.snapshot()
        started = time.monotonic()

        self.inner.reset()

        after = self.inner.snapshot()

        if self.trace is not None:
            self.trace.emit(
                "environment.reset",
                layer="environment",
                component=(
                    "LegalOfficeEnvironment"
                ),
                operation="reset",
                status="success",
                duration_ms=(
                    (
                        time.monotonic()
                        - started
                    )
                    * 1000
                ),
                details=snapshot_diff(
                    before,
                    after,
                ),
                visibility=(
                    "evaluator_only"
                ),
            )

    def close(self) -> None:
        if self.trace is not None:
            self.trace.emit(
                "environment.closed",
                layer="environment",
                component=(
                    "LegalOfficeEnvironment"
                ),
                operation="close",
                status="success",
            )

        self.inner.close()