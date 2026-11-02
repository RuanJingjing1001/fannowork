from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from fanno_work.tasking import (
    TaskBundle,
    TaskBundleError,
    validate_task_bundle,
)
from fanno_work.tasking.hashing import (
    bundle_digest,
    sha256_file,
)

from .models import (
    HarborCompileConfig,
    HarborTaskPackage,
)
from .validator import (
    HarborPackageError,
    validate_harbor_package,
)


_COMPILER_VERSION = "0.2.0"


_SETUP_TASK_PY = r'''from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def confined(
    root: Path,
    relative: str,
) -> Path:
    pure = PurePosixPath(relative)

    if (
        pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
    ):
        raise ValueError(
            f"unsafe task path: {relative!r}"
        )

    resolved = (root / pure).resolve()

    if not resolved.is_relative_to(
        root.resolve()
    ):
        raise ValueError(
            f"path escapes root: {relative!r}"
        )

    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
    )
    args = parser.parse_args()

    manifest = json.loads(
        args.manifest.read_text(
            encoding="utf-8"
        )
    )

    source_root = (
        args.source_root.resolve()
    )
    workspace = args.workspace.resolve()

    inputs_dir = workspace / "inputs"
    work_dir = workspace / "work"
    outputs_dir = workspace / "outputs"

    inputs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    outputs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    for item in manifest["inputs"]:
        source = confined(
            source_root,
            item["source"],
        )
        destination = confined(
            workspace,
            item["workspace_path"],
        )

        if not source.is_file():
            raise FileNotFoundError(
                f"input source missing: {source}"
            )

        expected = item.get("sha256")

        if (
            isinstance(expected, str)
            and sha256_file(source)
            != expected
        ):
            raise ValueError(
                "input source digest mismatch: "
                f"{item['source']}"
            )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.copy2(
            source,
            destination,
        )

        destination.chmod(0o444)

    work_dir.chmod(0o775)
    outputs_dir.chmod(0o775)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


_EVALUATE_PY = r'''from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import traceback

from fanno_work.evaluation import (
    EvaluationContext,
    RewardEngine,
    load_evaluator_spec,
)
from fanno_work.tasking import TaskBundle


def load_optional_json(
    path: Path,
) -> dict:
    if not path.is_file():
        return {}

    value = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(value, dict):
        raise ValueError(
            f"{path} must contain an object"
        )

    return value


def write_json(
    path: Path,
    value: dict,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path("/tests/bundle"),
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path(
            "/tmp/fanno-evaluation-run"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "/logs/verifier"
        ),
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(
            "/var/lib/fanno/state"
        ),
    )
    args = parser.parse_args()

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    reward_path = (
        args.output_dir / "reward.txt"
    )

    reward_path.write_text(
        "0.0\n",
        encoding="utf-8",
    )

    try:
        bundle = TaskBundle.load(
            args.bundle
        )

        evaluator = (
            load_evaluator_spec(
                args.bundle
                / "evaluator.json"
            )
        )

        context = (
            EvaluationContext.from_bundle(
                bundle,
                args.run_dir,
                initial_snapshot=(
                    load_optional_json(
                        args.state_dir
                        / "initial_snapshot.json"
                    )
                ),
                final_snapshot=(
                    load_optional_json(
                        args.state_dir
                        / "final_snapshot.json"
                    )
                ),
                reset_snapshot=(
                    load_optional_json(
                        args.state_dir
                        / "reset_snapshot.json"
                    )
                    if (
                        args.state_dir
                        / "reset_snapshot.json"
                    ).is_file()
                    else None
                ),
                metadata={
                    "execution_target": (
                        "harbor"
                    )
                },
            )
        )

        result = RewardEngine().evaluate(
            context,
            evaluator,
            output_dir=args.output_dir,
        )

        result_data = result.to_dict()

        write_json(
            args.output_dir
            / "details.json",
            result_data,
        )

        reward_path.write_text(
            f"{result.reward:.10g}\n",
            encoding="utf-8",
        )

        return 0

    except Exception as exc:
        write_json(
            args.output_dir
            / "evaluation-error.json",
            {
                "error_type": (
                    type(exc).__name__
                ),
                "message": str(exc),
                "traceback": (
                    traceback.format_exc()
                ),
            },
        )

        reward_path.write_text(
            "0.0\n",
            encoding="utf-8",
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


_TEST_SH = r'''#!/usr/bin/env bash
set -uo pipefail

RUN_DIR="/tmp/fanno-evaluation-run"
VERIFIER_DIR="/logs/verifier"
STATE_DIR="/var/lib/fanno/state"

mkdir -p "${VERIFIER_DIR}"
printf '0.0\n' > "${VERIFIER_DIR}/reward.txt"

fanno-control snapshot \
  --output "${STATE_DIR}/final_snapshot.json"

rm -rf "${RUN_DIR}"
mkdir -p \
  "${RUN_DIR}/artifacts" \
  "${RUN_DIR}/workspace/inputs"

if [ -d /workspace/outputs ]; then
  cp -a \
    /workspace/outputs/. \
    "${RUN_DIR}/artifacts/"
fi

if [ -d /workspace/inputs ]; then
  cp -a \
    /workspace/inputs/. \
    "${RUN_DIR}/workspace/inputs/"
fi

python3 /tests/evaluate.py \
  --bundle /tests/bundle \
  --run-dir "${RUN_DIR}" \
  --output-dir "${VERIFIER_DIR}" \
  --state-dir "${STATE_DIR}"
'''


def _write_text(
    path: Path,
    content: str,
    *,
    executable: bool = False,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        content,
        encoding="utf-8",
    )

    if executable:
        path.chmod(0o755)


def _write_json(
    path: Path,
    value: dict[str, Any],
) -> None:
    _write_text(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _toml_string(value: str) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
    )


def _toml_string_list(
    values: list[str],
) -> str:
    return "[{}]".format(
        ", ".join(
            _toml_string(value)
            for value in values
        )
    )


def _slug(value: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.casefold(),
    ).strip("-")

    if not normalized:
        raise HarborPackageError(
            "task id cannot be converted "
            "to a Harbor name"
        )

    return normalized[:120]


def _image_reference(
    bundle: TaskBundle,
) -> str:
    if not bundle.manifest.images:
        raise HarborPackageError(
            "bundle manifest does not declare "
            "a runtime image"
        )

    image = bundle.manifest.images[0]

    if image.digest:
        return (
            f"{image.reference}@"
            f"{image.digest}"
        )

    return image.reference


def _task_keywords(
    task: dict[str, Any],
) -> list[str]:
    values = {
        "fanno",
        "legal",
        "professional-workflow",
    }

    taxonomy = task.get(
        "taxonomy",
        {},
    )

    if isinstance(taxonomy, dict):
        for value in taxonomy.values():
            if isinstance(value, str):
                values.add(
                    value.replace("_", "-")
                )

            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        values.add(
                            item.replace(
                                "_",
                                "-",
                            )
                        )

    return sorted(values)


def _memory_to_mb(
    value: Any,
    default: int,
) -> int:
    if isinstance(value, int):
        return value

    if not isinstance(value, str):
        return default

    normalized = (
        value.strip().casefold()
    )

    match = re.fullmatch(
        r"(\d+)([kmg])?b?",
        normalized,
    )

    if match is None:
        return default

    amount = int(match.group(1))
    unit = match.group(2)

    if unit == "k":
        return max(1, amount // 1024)

    if unit == "g":
        return amount * 1024

    return amount


def _task_toml(
    bundle: TaskBundle,
    config: HarborCompileConfig,
) -> str:
    task = bundle.task
    environment = bundle.environment

    title = task.get(
        "title",
        bundle.manifest.bundle_id,
    )

    if not isinstance(title, str):
        title = bundle.manifest.bundle_id

    task_name = (
        f"fanno/{_slug(bundle.manifest.bundle_id)}"
    )

    container_config = (
        environment.get("config", {})
        .get("execution", {})
        .get("container", {})
    )

    if not isinstance(
        container_config,
        dict,
    ):
        container_config = {}

    cpus = container_config.get(
        "cpu_limit",
        config.cpus,
    )

    if not isinstance(cpus, (int, float)):
        cpus = config.cpus

    memory_mb = _memory_to_mb(
        container_config.get(
            "memory_limit"
        ),
        config.memory_mb,
    )

    keywords = _task_keywords(task)

    lines = [
        (
            "schema_version = "
            f"{_toml_string(config.schema_version)}"
        ),
        "",
        'artifacts = ["/workspace/outputs"]',
        "",
        "[task]",
        f"name = {_toml_string(task_name)}",
        (
            "version = "
            f"{_toml_string(config.task_version)}"
        ),
        (
            "description = "
            f"{_toml_string(title)}"
        ),
        (
            "keywords = "
            f"{_toml_string_list(keywords)}"
        ),
        "",
        "[metadata]",
        (
            "fanno_bundle_id = "
            f"{_toml_string(bundle.manifest.bundle_id)}"
        ),
        (
            "fanno_bundle_digest = "
            f"{_toml_string(bundle.manifest.bundle_digest)}"
        ),
        (
            "fanno_environment_id = "
            f"{_toml_string(bundle.manifest.environment['id'])}"
        ),
        (
            "fanno_environment_version = "
            f"{_toml_string(str(bundle.manifest.environment['version']))}"
        ),
        (
            "fanno_evaluator_id = "
            f"{_toml_string(str(bundle.manifest.evaluator['id']))}"
        ),
        (
            "fanno_evaluator_version = "
            f"{_toml_string(str(bundle.manifest.evaluator['version']))}"
        ),
        (
            "fanno_compiler_version = "
            f"{_toml_string(_COMPILER_VERSION)}"
        ),
        "",
        "[agent]",
        (
            "timeout_sec = "
            f"{config.agent_timeout_sec}"
        ),
        'user = "agent"',
        "",
        "[verifier]",
        (
            "timeout_sec = "
            f"{config.verifier_timeout_sec}"
        ),
        'user = "root"',
        'environment_mode = "shared"',
        "",
        "[environment]",
        'os = "linux"',
        'workdir = "/workspace"',
        (
            "network_mode = "
            f"{_toml_string(config.network_mode)}"
        ),
        (
            "build_timeout_sec = "
            f"{config.build_timeout_sec}"
        ),
        f"cpus = {float(cpus)}",
        f"memory_mb = {memory_mb}",
        (
            "storage_mb = "
            f"{config.storage_mb}"
        ),
        "gpus = 0",
        "",
    ]

    return "\n".join(lines)


def _instruction_markdown(
    bundle: TaskBundle,
) -> str:
    task = bundle.task

    title = task.get(
        "title",
        bundle.manifest.bundle_id,
    )
    instruction = task.get(
        "instruction"
    )

    if (
        not isinstance(instruction, str)
        or not instruction.strip()
    ):
        raise HarborPackageError(
            "task instruction must be "
            "a non-empty string"
        )

    return (
        f"# {title}\n\n"
        f"{instruction.strip()}\n\n"
        "## Environment tools\n\n"
        "Use `fanno-tool actions` to inspect the "
        "available Action contracts.\n\n"
        "Use `fanno-tool observe` to obtain the "
        "initial Agent-visible observation.\n\n"
        "Execute an Action with:\n\n"
        "```bash\n"
        "fanno-tool call ACTION_ID "
        "--arguments '{\"field\":\"value\"}'\n"
        "```\n\n"
        "Do not attempt to call environment reset, "
        "snapshot, evaluator, or control interfaces.\n"
    )

def _dockerfile(
    runtime_image: str,
) -> str:
    return f"""ARG RUNTIME_IMAGE={runtime_image}
FROM ${{RUNTIME_IMAGE}}

USER root

RUN if ! getent group fanno-agent >/dev/null; then \\
      groupadd --gid 20000 fanno-agent; \\
    fi \\
    && if ! id -u agent >/dev/null 2>&1; then \\
      useradd --create-home --shell /bin/bash agent; \\
    fi \\
    && usermod -a -G fanno-agent agent

COPY public-inputs/ /opt/fanno/public-inputs/
COPY private/ /var/lib/fanno/private/

RUN chmod -R go-rwx /var/lib/fanno/private \\
    && mkdir -p \\
         /workspace \\
         /run/fanno \\
         /var/lib/fanno/run \\
         /var/lib/fanno/state \\
    && chown root:fanno-agent /workspace /run/fanno \\
    && chmod 2770 /workspace /run/fanno

ENV FANNO_AGENT_GROUP=fanno-agent
ENV FANNO_AGENT_GID=20000
ENV FANNO_AGENT_SOCKET=/run/fanno/agent.sock
ENV FANNO_CONTROL_SOCKET=/run/fanno/control.sock
ENV FANNO_WORKSPACE=/workspace
ENV FANNO_RUN_DIR=/var/lib/fanno/run
ENV FANNO_STATE_DIR=/var/lib/fanno/state
ENV FANNO_PRIVATE_TASK_ROOT=/var/lib/fanno/private
ENV FANNO_PUBLIC_INPUT_ROOT=/opt/fanno/public-inputs

WORKDIR /workspace

CMD ["python", "-m", "fanno_legal.runtime.entrypoint"]
"""

def _compose_yaml(
    bundle: TaskBundle,
) -> str:
    environment = bundle.environment
    config = environment.get(
        "config",
        {},
    )

    if not isinstance(config, dict):
        config = {}

    enabled_actions = config.get(
        "enabled_actions",
        [],
    )

    if not isinstance(
        enabled_actions,
        list,
    ):
        enabled_actions = []

    email_enabled = any(
        isinstance(action, str)
        and action.startswith("email.")
        for action in enabled_actions
    )
    calendar_enabled = any(
        isinstance(action, str)
        and action.startswith("calendar.")
        for action in enabled_actions
    )

    dependencies: list[str] = []

    if email_enabled:
        dependencies.append("greenmail")

    if calendar_enabled:
        dependencies.append("radicale")

    lines = [
        "services:",
        "  main:",
        "    build:",
        "      context: .",
        "      dockerfile: Dockerfile",
        "    command:",
        "      - sh",
        "      - -lc",
        "      - sleep infinity",
        "    environment:",
        (
            "      FANNO_SERVICE_LIFECYCLE: "
            "external"
        ),
        (
            "      FANNO_PRIVATE_TASK_ROOT: "
            "/var/lib/fanno/private"
        ),
        (
            "      FANNO_WORKSPACE: "
            "/workspace"
        ),
    ]
def _compose_yaml(
    bundle: TaskBundle,
) -> str:
    environment = bundle.environment
    config = environment.get(
        "config",
        {},
    )

    if not isinstance(config, dict):
        config = {}

    enabled_actions = config.get(
        "enabled_actions",
        [],
    )

    if not isinstance(
        enabled_actions,
        list,
    ):
        enabled_actions = []

    email_enabled = any(
        isinstance(action, str)
        and action.startswith("email.")
        for action in enabled_actions
    )

    calendar_enabled = any(
        isinstance(action, str)
        and action.startswith("calendar.")
        for action in enabled_actions
    )

    lines = [
        "services:",
        "  main:",
        "    build:",
        "      context: .",
        "      dockerfile: Dockerfile",
        "    command:",
        "      - python",
        "      - -m",
        (
            "      - "
            "fanno_legal.runtime.entrypoint"
        ),
        "    environment:",
        (
            "      FANNO_SERVICE_LIFECYCLE: "
            "external"
        ),
        (
            "      FANNO_PRIVATE_TASK_ROOT: "
            "/var/lib/fanno/private"
        ),
        (
            "      FANNO_PUBLIC_INPUT_ROOT: "
            "/opt/fanno/public-inputs"
        ),
        (
            "      FANNO_WORKSPACE: "
            "/workspace"
        ),
        (
            "      FANNO_RUN_DIR: "
            "/var/lib/fanno/run"
        ),
        (
            "      FANNO_STATE_DIR: "
            "/var/lib/fanno/state"
        ),
        (
            "      FANNO_AGENT_GID: "
            "\"20000\""
        ),
        (
            "      FANNO_TRACE_CONSOLE: "
            "\"1\""
        ),
    ]

    if email_enabled:
        lines.extend(
            [
                (
                    "      FANNO_GREENMAIL_HOST: "
                    "127.0.0.1"
                ),
                (
                    "      FANNO_GREENMAIL_IMAP_PORT: "
                    "\"3143\""
                ),
                (
                    "      FANNO_GREENMAIL_SMTP_PORT: "
                    "\"3025\""
                ),
            ]
        )

    if calendar_enabled:
        lines.extend(
            [
                (
                    "      FANNO_RADICALE_URL: "
                    "http://127.0.0.1:5232"
                ),
            ]
        )

    dependencies: list[str] = []

    if email_enabled:
        dependencies.append("greenmail")

    if calendar_enabled:
        dependencies.append("radicale")

    if dependencies:
        lines.append("    depends_on:")

        for dependency in dependencies:
            lines.extend(
                [
                    f"      {dependency}:",
                    (
                        "        condition: "
                        "service_started"
                    ),
                ]
            )

    lines.extend(
        [
            "    healthcheck:",
            (
                "      test: "
                "[\"CMD\", \"fanno-tool\", "
                "\"status\"]"
            ),
            "      interval: 5s",
            "      timeout: 3s",
            "      retries: 30",
            "      start_period: 10s",
        ]
    )

    if email_enabled:
        email_config = config.get(
            "email",
            {},
        )

        if not isinstance(
            email_config,
            dict,
        ):
            email_config = {}

        service_config = (
            email_config.get(
                "service",
                {},
            )
        )

        if not isinstance(
            service_config,
            dict,
        ):
            service_config = {}

        image = service_config.get(
            "image",
            "greenmail/standalone:2.1.8",
        )

        lines.extend(
            [
                "",
                "  greenmail:",
                (
                    "    image: "
                    f"{json.dumps(str(image))}"
                ),
                "    environment:",
                (
                    "      GREENMAIL_OPTS: "
                    "\"-Dgreenmail.setup.test.all "
                    "-Dgreenmail.hostname=0.0.0.0 "
                    "-Dgreenmail.auth.disabled\""
                ),
            ]
        )

    if calendar_enabled:
        calendar_config = config.get(
            "calendar",
            {},
        )

        if not isinstance(
            calendar_config,
            dict,
        ):
            calendar_config = {}

        service_config = (
            calendar_config.get(
                "service",
                {},
            )
        )

        if not isinstance(
            service_config,
            dict,
        ):
            service_config = {}

        image = service_config.get(
            "image",
            "tomsquest/docker-radicale:latest",
        )

        lines.extend(
            [
                "",
                "  radicale:",
                (
                    "    image: "
                    f"{json.dumps(str(image))}"
                ),
            ]
        )

    lines.append("")

    return "\n".join(lines)
def _safe_replace_check(
    target: Path,
    bundle_id: str,
) -> None:
    if target.is_symlink():
        raise HarborPackageError(
            "refusing to replace a symlink: "
            f"{target}"
        )

    if not target.is_dir():
        raise HarborPackageError(
            "refusing to replace a non-directory: "
            f"{target}"
        )

    if not any(target.iterdir()):
        return

    manifest_path = (
        target / "harbor-manifest.json"
    )

    if not manifest_path.is_file():
        raise HarborPackageError(
            "refusing to replace a non-Harbor "
            f"package directory: {target}"
        )

    value = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    if value.get(
        "source_bundle_id"
    ) != bundle_id:
        raise HarborPackageError(
            "refusing to replace a Harbor "
            "package generated from a "
            "different bundle"
        )


def _copy_public_inputs(
    bundle: TaskBundle,
    environment_dir: Path,
) -> dict[str, Any]:
    task_inputs = bundle.task.get(
        "inputs",
        [],
    )

    if not isinstance(task_inputs, list):
        raise HarborPackageError(
            "task inputs must be a list"
        )

    public_root = (
        environment_dir / "public-inputs"
    )
    public_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    input_manifest: list[
        dict[str, Any]
    ] = []

    public_sources: set[str] = set()

    for item in task_inputs:
        if not isinstance(item, dict):
            raise HarborPackageError(
                "task inputs must contain "
                "objects"
            )

        source = item.get("source")
        workspace_path = item.get("path")

        if not isinstance(
            source,
            str,
        ) or not isinstance(
            workspace_path,
            str,
        ):
            raise HarborPackageError(
                "task input source and path "
                "must be strings"
            )

        source_path = (
            bundle.root / source
        ).resolve()

        if not source_path.is_relative_to(
            bundle.root
        ):
            raise HarborPackageError(
                "task input source escapes "
                "bundle"
            )

        if not source_path.is_file():
            raise HarborPackageError(
                "task input source is missing: "
                f"{source}"
            )

        destination = (
            public_root / source
        )
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.copy2(
            source_path,
            destination,
        )

        expected_digest = item.get(
            "sha256"
        )

        if not isinstance(
            expected_digest,
            str,
        ):
            expected_digest = (
                sha256_file(source_path)
            )

        input_manifest.append(
            {
                "source": source,
                "workspace_path": (
                    f"inputs/{workspace_path}"
                ),
                "sha256": expected_digest,
            }
        )

        public_sources.add(source)

    if not input_manifest:
        _write_text(
            public_root / ".keep",
            "",
        )

    _write_json(
        environment_dir
        / "input-manifest.json",
        {
            "schema_version": 1,
            "inputs": input_manifest,
        },
    )

    return {
        "public_sources": public_sources,
    }


def _copy_private_runtime_data(
    bundle: TaskBundle,
    environment_dir: Path,
    public_sources: set[str],
) -> None:
    private_root = (
        environment_dir / "private"
    )

    private_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    for filename in (
        "task.json",
        "actions.json",
        "seed.json",
    ):
        shutil.copy2(
            bundle.root / filename,
            private_root / filename,
        )

    runtime_environment = json.loads(
        json.dumps(
            bundle.environment,
            ensure_ascii=False,
        )
    )

    config = runtime_environment.setdefault(
        "config",
        {},
    )
    execution = config.setdefault(
        "execution",
        {},
    )
    execution["backend"] = "in_container"
    config["service_lifecycle"] = (
        "external"
    )

    _write_json(
        private_root / "environment.json",
        runtime_environment,
    )

    for item in bundle.manifest.files:
        if item.role != "task_asset":
            continue

        if item.path in public_sources:
            continue

        source = bundle.root / item.path
        destination = (
            private_root / item.path
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            source,
            destination,
        )

def _build_file_inventory(
    root: Path,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        relative = path.relative_to(
            root
        ).as_posix()

        if relative == "harbor-manifest.json":
            continue

        result.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": (
                    path.stat().st_size
                ),
            }
        )

    return result


class HarborTaskCompiler:
    def compile(
        self,
        *,
        bundle_dir: str | Path,
        output_dir: str | Path,
        config: (
            HarborCompileConfig | None
        ) = None,
        force: bool = False,
    ) -> HarborTaskPackage:
        config = (
            config or HarborCompileConfig()
        )

        bundle_root = Path(
            bundle_dir
        ).resolve()

        validate_task_bundle(
            bundle_root
        )

        bundle = TaskBundle.load(
            bundle_root
        )

        target = Path(
            output_dir
        ).resolve()

        if target.exists() and not force:
            raise HarborPackageError(
                "Harbor output already exists: "
                f"{target}"
            )

        if target.exists():
            _safe_replace_check(
                target,
                bundle.manifest.bundle_id,
            )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{target.name}.",
                dir=target.parent,
            )
        )

        try:
            environment_dir = (
                temporary / "environment"
            )
            tests_dir = (
                temporary / "tests"
            )

            environment_dir.mkdir(
                parents=True,
                exist_ok=True,
            )
            tests_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            _write_text(
                temporary / "task.toml",
                _task_toml(
                    bundle,
                    config,
                ),
            )

            _write_text(
                temporary
                / "instruction.md",
                _instruction_markdown(
                    bundle
                ),
            )

            public_result = (
                _copy_public_inputs(
                    bundle,
                    environment_dir,
                )
            )

            _copy_private_runtime_data(
                bundle,
                environment_dir,
                public_result[
                    "public_sources"
                ],
            )

            _write_text(
                environment_dir
                / "Dockerfile",
                _dockerfile(
                    _image_reference(bundle)
                ),
            )

            _write_text(
                environment_dir
                / "docker-compose.yaml",
                _compose_yaml(bundle),
            )

            _write_text(
                environment_dir
                / "setup_task.py",
                _SETUP_TASK_PY,
            )

            _write_text(
                environment_dir
                / ".dockerignore",
                "__pycache__/\n*.pyc\n",
            )

            _write_text(
                tests_dir / "test.sh",
                _TEST_SH,
                executable=True,
            )

            _write_text(
                tests_dir / "evaluate.py",
                _EVALUATE_PY,
            )

            shutil.copytree(
                bundle.root,
                tests_dir / "bundle",
            )

            _write_text(
                temporary / "README.md",
                (
                    "# Generated Harbor Task\n\n"
                    "This directory was generated "
                    "from the Fanno canonical bundle "
                    f"`{bundle.manifest.bundle_id}`.\n\n"
                    "Do not edit generated files by "
                    "hand. Rebuild them from the "
                    "canonical bundle.\n"
                ),
            )

            inventory = (
                _build_file_inventory(
                    temporary
                )
            )

            package_digest = bundle_digest(
                (
                    item["path"],
                    item["sha256"],
                )
                for item in inventory
            )

            harbor_manifest = {
                "schema_version": 1,
                "compiler": {
                    "name": (
                        "fanno-harbor-compiler"
                    ),
                    "version": (
                        _COMPILER_VERSION
                    ),
                },
                "harbor": {
                    "task_schema_version": (
                        config.schema_version
                    ),
                },
                "source_bundle_id": (
                    bundle.manifest.bundle_id
                ),
                "source_bundle_digest": (
                    bundle.manifest
                    .bundle_digest
                ),
                "task_name": (
                    "fanno/"
                    f"{_slug(bundle.manifest.bundle_id)}"
                ),
                "package_digest": (
                    package_digest
                ),
                "files": inventory,
                "security": {
                    "evaluator_visible_to_agent": (
                        False
                    ),
                    "seed_location": (
                        "/var/lib/fanno/private"
                    ),
                    "agent_user": "agent",
                    "verifier_user": "root",
                },
                "status": (
                    "compiled_not_runtime_verified"
                ),
            }

            _write_json(
                temporary
                / "harbor-manifest.json",
                harbor_manifest,
            )

            validate_harbor_package(
                temporary
            )

            if target.exists():
                shutil.rmtree(target)

            os.replace(
                temporary,
                target,
            )

        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

        manifest = validate_harbor_package(
            target
        )

        return HarborTaskPackage(
            root=target,
            task_name=manifest["task_name"],
            source_bundle_id=(
                manifest[
                    "source_bundle_id"
                ]
            ),
            source_bundle_digest=(
                manifest[
                    "source_bundle_digest"
                ]
            ),
            package_digest=(
                manifest["package_digest"]
            ),
        )
