from __future__ import annotations

from dataclasses import replace
import grp
import json
import os
from pathlib import Path
import shutil
import signal
import threading
from typing import Any

from fanno_work.gateway import (
    EnvironmentGateway,
    EnvironmentGatewayServer,
)
from fanno_work.specs import (
    load_environment,
    load_task,
)

from fanno_legal.runtime.observed_environment import (
    ObservedLegalOfficeEnvironment,
)


def _write_json(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(path)


def _clear_directory(
    path: Path,
) -> None:
    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> int:
    os.chdir("/")

    private_root = Path(
        os.environ.get(
            "FANNO_PRIVATE_TASK_ROOT",
            "/var/lib/fanno/private",
        )
    ).resolve()

    public_root = Path(
        os.environ.get(
            "FANNO_PUBLIC_INPUT_ROOT",
            "/opt/fanno/public-inputs",
        )
    ).resolve()

    run_dir = Path(
        os.environ.get(
            "FANNO_RUN_DIR",
            "/var/lib/fanno/run",
        )
    ).resolve()

    state_dir = Path(
        os.environ.get(
            "FANNO_STATE_DIR",
            "/var/lib/fanno/state",
        )
    ).resolve()

    workspace = Path(
        os.environ.get(
            "FANNO_WORKSPACE",
            "/workspace",
        )
    ).resolve()

    _clear_directory(run_dir)
    _clear_directory(workspace)

    state_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    task = load_task(
        private_root / "task.json",
        verify_assets=False,
    )

    task = replace(
        task,
        source_dir=public_root,
    )

    spec = load_environment(
        private_root / "environment.json"
    )

    execution = dict(
        spec.config.get(
            "execution",
            {},
        )
    )
    execution["backend"] = (
        "in_container"
    )

    config = dict(spec.config)
    config["execution"] = execution

    spec = replace(
        spec,
        config=config,
    )

    environment = (
        ObservedLegalOfficeEnvironment(
            spec
        )
    )

    environment.prepare(
        task,
        run_dir,
    )

    initial_snapshot = (
        environment.snapshot()
    )

    _write_json(
        state_dir
        / "initial_snapshot.json",
        initial_snapshot,
    )

    group_name = os.environ.get(
        "FANNO_AGENT_GROUP",
        "fanno-agent",
    )

    group_id = grp.getgrnam(
        group_name
    ).gr_gid

    agent_server = (
        EnvironmentGatewayServer(
            socket_path=os.environ.get(
                "FANNO_AGENT_SOCKET",
                "/run/fanno/agent.sock",
            ),
            gateway=EnvironmentGateway(
                environment,
                role="agent",
            ),
            mode=0o660,
            group_id=group_id,
        )
    )

    control_server = (
        EnvironmentGatewayServer(
            socket_path=os.environ.get(
                "FANNO_CONTROL_SOCKET",
                "/run/fanno/control.sock",
            ),
            gateway=EnvironmentGateway(
                environment,
                role="control",
            ),
            mode=0o600,
        )
    )

    agent_server.start()
    control_server.start()

    stopping = threading.Event()

    def stop_handler(
        signum: int,
        frame: object,
    ) -> None:
        stopping.set()

    signal.signal(
        signal.SIGTERM,
        stop_handler,
    )
    signal.signal(
        signal.SIGINT,
        stop_handler,
    )

    try:
        while not stopping.wait(1):
            pass

    finally:
        try:
            _write_json(
                state_dir
                / "final_snapshot.json",
                environment.snapshot(),
            )
        finally:
            agent_server.close()
            control_server.close()
            environment.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())