from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agents import ScriptedAgent
from .models import ActionRequest
from .registry import default_registry
from .runtime import WorkflowRunner
from .specs import load_environment, load_task, validate_pair
from .tasking import (
    TaskBundleResolver,
    validate_task_bundle,
)


def _load_actions(path: Path) -> list[ActionRequest]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("actions file must contain a JSON array")
    return [ActionRequest(name=item["name"], arguments=dict(item.get("arguments", {}))) for item in value]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fanno-work")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("plugins", help="List installed environment and verifier adapters")
    validate = sub.add_parser("validate", help="Validate a task/environment pair")
    validate.add_argument("--task", type=Path, required=True)
    validate.add_argument("--environment", type=Path, required=True)
    run = sub.add_parser("run", help="Run an explicit action sequence")
    run.add_argument("--task", type=Path, required=True)
    run.add_argument("--environment", type=Path, required=True)
    run.add_argument("--actions", type=Path, required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--skip-reset-check", action="store_true")

    task_command = sub.add_parser(
        "task",
        help="Build and validate canonical task bundles",
    )
    task_subcommands = task_command.add_subparsers(
        dest="task_command",
        required=True,
    )

    task_build = task_subcommands.add_parser(
        "build",
        help="Build a deterministic canonical task bundle",
    )
    task_build.add_argument(
        "--task",
        type=Path,
        required=True,
    )
    task_build.add_argument(
        "--environment",
        type=Path,
        required=True,
    )
    task_build.add_argument(
        "--evaluator",
        type=Path,
    )
    task_build.add_argument(
        "--output",
        type=Path,
    )
    task_build.add_argument(
        "--force",
        action="store_true",
    )

    task_validate = task_subcommands.add_parser(
        "validate",
        help="Validate a canonical task bundle",
    )
    task_validate.add_argument(
        "--bundle",
        type=Path,
        required=True,
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)

    if args.command == "task":
        if args.task_command == "validate":
            manifest = validate_task_bundle(
                args.bundle
            )
            print(
                json.dumps(
                    {
                        "valid": True,
                        "bundle_id": (
                            manifest.bundle_id
                        ),
                        "bundle_digest": (
                            manifest.bundle_digest
                        ),
                        "file_count": len(
                            manifest.files
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        task = load_task(args.task)
        output = args.output
        if output is None:
            output = (
                Path("build")
                / "tasks"
                / task.id
                / "canonical"
            )

        bundle = TaskBundleResolver().build(
            task_path=args.task,
            environment_path=args.environment,
            evaluator_path=args.evaluator,
            output_dir=output,
            force=args.force,
        )
        print(
            json.dumps(
                {
                    "built": True,
                    "bundle_id": (
                        bundle.manifest.bundle_id
                    ),
                    "bundle_digest": (
                        bundle.manifest.bundle_digest
                    ),
                    "file_count": len(
                        bundle.manifest.files
                    ),
                    "output": str(
                        bundle.root
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    registry = default_registry()
    if args.command == "plugins":
        print(json.dumps(registry.describe(), indent=2, sort_keys=True))
        return 0
    task = load_task(args.task)
    environment = load_environment(args.environment)
    validate_pair(task, environment)
    if args.command == "validate":
        print(json.dumps({"valid": True, "task": task.id, "environment": environment.id}, indent=2))
        return 0
    manifest = WorkflowRunner(registry).run(
        task,
        environment,
        ScriptedAgent(_load_actions(args.actions)),
        args.run_dir,
        verify_reset=not args.skip_reset_check,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
