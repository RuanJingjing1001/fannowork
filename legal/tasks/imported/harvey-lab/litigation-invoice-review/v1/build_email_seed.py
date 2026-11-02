from __future__ import annotations

from email import policy
from email.parser import BytesParser
import json
from pathlib import Path


ROOT = Path(__file__).parent
SOURCE_EMAIL = (
    ROOT
    / "assets"
    / "holt-email-research-concerns.eml"
)
OUTPUT = ROOT / "seed.json"


def main() -> None:
    message = BytesParser(
        policy=policy.default
    ).parsebytes(
        SOURCE_EMAIL.read_bytes()
    )

    body_part = message.get_body(
        preferencelist=("plain",)
    )

    if body_part is None:
        raise RuntimeError(
            "source email has no text/plain body"
        )

    body = body_part.get_content()

    seed = {
        "schema_version": 1,
        "actors": {
            "agent": {
                "roles": [
                    "legal_invoice_reviewer"
                ]
            },
            "evaluator": {
                "roles": [
                    "trusted_evaluator"
                ]
            },
        },
        "collections": [],
        "policies": [],
        "email": {
            "accounts": {
                "agent": {
                    "address": (
                        "dhargrove@"
                        "hargrovelinden.com"
                    ),
                    "password": (
                        "agent-test-password"
                    ),
                    "display_name": (
                        "Diane K. Hargrove"
                    ),
                },
                "marcus_holt": {
                    "address": (
                        "mholt@"
                        "terraverde-env.com"
                    ),
                    "password": (
                        "marcus-test-password"
                    ),
                    "display_name": (
                        "Marcus Holt"
                    ),
                },
                "priya_anand": {
                    "address": (
                        "panand@"
                        "terraverde-env.com"
                    ),
                    "password": (
                        "priya-test-password"
                    ),
                    "display_name": (
                        "Priya Anand"
                    ),
                },
            },
            "messages": [
                {
                    "from_account": (
                        "marcus_holt"
                    ),
                    "to_accounts": [
                        "agent"
                    ],
                    "cc_accounts": [
                        "priya_anand"
                    ],
                    "subject": str(
                        message["Subject"]
                    ),
                    "body": body,
                    "date": str(
                        message["Date"]
                    ),
                    "source": {
                        "type": (
                            "imported_eml"
                        ),
                        "path": (
                            "assets/"
                            "holt-email-"
                            "research-concerns.eml"
                        ),
                    },
                }
            ],
        },
    }

    OUTPUT.write_text(
        json.dumps(
            seed,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()