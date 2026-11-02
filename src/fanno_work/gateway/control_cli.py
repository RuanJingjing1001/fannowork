from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .client import (
    GatewayClient,
    GatewayClientError,
)


def _write_json(
    path: Path,
    value: Any,
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


def main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="fanno-control"
    )

    parser.add_argument(
        "--socket",
        default=os.environ.get(
            "FANNO_CONTROL_SOCKET",
            "/run/fanno/control.sock",
        ),
    )

    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subcommands.add_parser("status")

    snapshot = subcommands.add_parser(
        "snapshot"
    )
    snapshot.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    subcommands.add_parser("reset")

    args = parser.parse_args(argv)

    client = GatewayClient(
        args.socket
    )

    try:
        if args.command == "status":
            result = client.call("status")

        elif args.command == "snapshot":
            result = client.call(
                "snapshot"
            )
            _write_json(
                args.output,
                result,
            )

        else:
            result = client.call("reset")

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    except GatewayClientError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.code,
                    "message": exc.message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())