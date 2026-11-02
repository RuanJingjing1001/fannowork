from __future__ import annotations

import argparse
import json
import os
from typing import Any

from .client import (
    GatewayClient,
    GatewayClientError,
)


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)

    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError(
            "arguments must contain a JSON object"
        )

    return parsed


def _print(value: Any) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="fanno-tool"
    )

    parser.add_argument(
        "--socket",
        default=os.environ.get(
            "FANNO_AGENT_SOCKET",
            "/run/fanno/agent.sock",
        ),
    )

    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subcommands.add_parser("status")
    subcommands.add_parser("observe")
    subcommands.add_parser("actions")

    call_parser = subcommands.add_parser(
        "call"
    )
    call_parser.add_argument("action")
    call_parser.add_argument(
        "--arguments",
        type=_json_object,
        default={},
    )

    args = parser.parse_args(argv)

    client = GatewayClient(
        args.socket
    )

    try:
        if args.command == "status":
            result = client.call("status")

        elif args.command == "observe":
            result = client.call("observe")

        elif args.command == "actions":
            result = client.call("actions")

        else:
            result = client.call(
                "call",
                {
                    "action": args.action,
                    "arguments": args.arguments,
                },
            )

        _print(result)

        if (
            args.command == "call"
            and isinstance(result, dict)
            and result.get("ok") is False
        ):
            return 2

        return 0

    except GatewayClientError as exc:
        _print(
            {
                "ok": False,
                "error": exc.code,
                "message": exc.message,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())