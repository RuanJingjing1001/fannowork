from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import HarborTaskCompiler
from .models import HarborCompileConfig
from .validator import (
    validate_harbor_package,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="fanno-harbor"
    )

    subcommands = root.add_subparsers(
        dest="command",
        required=True,
    )

    compile_command = subcommands.add_parser(
        "compile",
        help=(
            "Compile a canonical Fanno bundle "
            "into a Harbor task"
        ),
    )

    compile_command.add_argument(
        "--bundle",
        type=Path,
        required=True,
    )
    compile_command.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    compile_command.add_argument(
        "--force",
        action="store_true",
    )
    compile_command.add_argument(
        "--network-mode",
        choices=[
            "public",
            "no-network",
            "allowlist",
        ],
        default="no-network",
    )
    compile_command.add_argument(
        "--agent-timeout",
        type=float,
        default=1800.0,
    )
    compile_command.add_argument(
        "--verifier-timeout",
        type=float,
        default=300.0,
    )

    validate_command = (
        subcommands.add_parser(
            "validate",
            help=(
                "Validate a generated Harbor "
                "task package"
            ),
        )
    )

    validate_command.add_argument(
        "--package",
        type=Path,
        required=True,
    )

    return root


def main(
    argv: list[str] | None = None,
) -> int:
    args = parser().parse_args(argv)

    if args.command == "validate":
        manifest = validate_harbor_package(
            args.package
        )

        print(
            json.dumps(
                {
                    "valid": True,
                    "task_name": (
                        manifest["task_name"]
                    ),
                    "source_bundle_id": (
                        manifest[
                            "source_bundle_id"
                        ]
                    ),
                    "package_digest": (
                        manifest[
                            "package_digest"
                        ]
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

        return 0

    package = HarborTaskCompiler().compile(
        bundle_dir=args.bundle,
        output_dir=args.output,
        force=args.force,
        config=HarborCompileConfig(
            network_mode=(
                args.network_mode
            ),
            agent_timeout_sec=(
                args.agent_timeout
            ),
            verifier_timeout_sec=(
                args.verifier_timeout
            ),
        ),
    )

    print(
        json.dumps(
            {
                "compiled": True,
                "task_name": (
                    package.task_name
                ),
                "source_bundle_id": (
                    package.source_bundle_id
                ),
                "source_bundle_digest": (
                    package
                    .source_bundle_digest
                ),
                "package_digest": (
                    package.package_digest
                ),
                "output": str(package.root),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())