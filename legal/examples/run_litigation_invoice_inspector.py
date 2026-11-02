from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from fanno_work.models import (
    ActionRequest,
    to_jsonable,
)
from fanno_work.runtime.recorder import (
    TrajectoryRecorder,
)
from fanno_work.specs import (
    load_environment,
    load_task,
)

from fanno_legal.runtime.observed_environment import (
    ObservedLegalOfficeEnvironment,
)


LEGAL_ROOT = Path(__file__).parents[1]

TASK_PATH = (
    LEGAL_ROOT
    / "tasks"
    / "imported"
    / "harvey-lab"
    / "litigation-invoice-review"
    / "v1"
    / "task.json"
)

ENVIRONMENT_PATH = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)


def make_run_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        "litigation-invoice-review-"
        + timestamp
    )


def verify_docx_independently(
    path: Path,
) -> dict:
    checks: list[dict] = []

    exists = path.is_file()
    checks.append(
        {
            "id": "artifact_exists",
            "passed": exists,
            "evidence": {
                "path": str(path),
            },
        }
    )

    if not exists:
        return {
            "passed": False,
            "checker": "independent_ooxml",
            "checks": checks,
        }

    content = path.read_bytes()
    checks.append(
        {
            "id": "artifact_non_empty",
            "passed": bool(content),
            "evidence": {
                "size": len(content),
                "sha256": hashlib.sha256(
                    content
                ).hexdigest(),
            },
        }
    )

    text = ""
    table_count = 0
    package_valid = False
    package_error: str | None = None

    namespace = (
        "{http://schemas.openxmlformats.org/"
        "wordprocessingml/2006/main}"
    )

    try:
        with ZipFile(path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise BadZipFile(
                    f"corrupt member: {corrupt_member}"
                )

            document_xml = archive.read(
                "word/document.xml"
            )

        root = ET.fromstring(
            document_xml
        )

        text = "\n".join(
            node.text or ""
            for node in root.iter(
                f"{namespace}t"
            )
        )

        table_count = sum(
            1
            for _ in root.iter(
                f"{namespace}tbl"
            )
        )
        package_valid = True
    except (
        BadZipFile,
        KeyError,
        OSError,
        ET.ParseError,
    ) as exc:
        package_error = (
            f"{type(exc).__name__}: {exc}"
        )

    checks.append(
        {
            "id": "valid_docx_package",
            "passed": package_valid,
            "evidence": {
                "error": package_error,
            },
        }
    )

    expected_text = [
        "Executive Summary",
        (
            "This is an environment execution "
            "test, not the final legal analysis."
        ),
        "Westlaw disbursement",
        "Section 9.3",
        "$4,850.00",
    ]

    for expected in expected_text:
        checks.append(
            {
                "id": (
                    "text_present:"
                    + expected[:40]
                ),
                "passed": expected in text,
                "evidence": {
                    "expected": expected,
                },
            }
        )

    checks.append(
        {
            "id": "summary_table_present",
            "passed": table_count >= 1,
            "evidence": {
                "table_count": table_count,
            },
        }
    )

    return {
        "passed": all(
            check["passed"]
            for check in checks
        ),
        "checker": "independent_ooxml",
        "text_character_count": len(text),
        "table_count": table_count,
        "checks": checks,
    }


def main() -> int:
    run_id = make_run_id()

    run_dir = (
        LEGAL_ROOT
        / "runs"
        / "litigation-invoice-review"
        / run_id
    )

    run_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    task = load_task(
        TASK_PATH,
        verify_assets=True,
    )

    environment_spec = load_environment(
        ENVIRONMENT_PATH
    )

    environment = (
        ObservedLegalOfficeEnvironment(
            environment_spec
        )
    )

    recorder = TrajectoryRecorder(
        run_dir / "trajectory.jsonl"
    )

    actions = [
        ActionRequest(
            name="filesystem.list",
            arguments={
                "path": "inputs",
            },
        ),
        ActionRequest(
            name=(
                "spreadsheet."
                "inspect_workbook"
            ),
            arguments={
                "path": (
                    "inputs/"
                    "hl-july-2024-invoice.xlsx"
                ),
            },
        ),
        ActionRequest(
            name="spreadsheet.read_range",
            arguments={
                "path": (
                    "inputs/"
                    "hl-july-2024-invoice.xlsx"
                ),
                "sheet": "Fee Detail",
                "range": "A1:I3",
            },
        ),
        ActionRequest(
            name="spreadsheet.find",
            arguments={
                "path": (
                    "inputs/"
                    "hl-july-2024-invoice.xlsx"
                ),
                "query": "Westlaw",
                "case_sensitive": False,
                "max_results": 20,
            },
        ),
        ActionRequest(
            name="document.extract_text",
            arguments={
                "path": (
                    "inputs/"
                    "terraverde-"
                    "billing-guidelines.docx"
                ),
            },
        ),
        ActionRequest(
            name="email.search",
            arguments={
                "subject": (
                    "Research Billing Concerns"
                ),
                "sender": (
                    "mholt@terraverde-env.com"
                ),
                "max_results": 10,
            },
        ),
                ActionRequest(
            name="document.create",
            arguments={
                "path": (
                    "work/"
                    "research-charge-review-memo.odt"
                ),
                "title": (
                    "Litigation Invoice "
                    "Research Charge Review"
                ),
            },
        ),
        ActionRequest(
            name="document.insert_heading",
            arguments={
                "path": (
                    "work/"
                    "research-charge-review-memo.odt"
                ),
                "text": "Executive Summary",
                "level": 1,
            },
        ),
        ActionRequest(
            name="document.insert_paragraph",
            arguments={
                "path": (
                    "work/"
                    "research-charge-review-memo.odt"
                ),
                "text": (
                    "This is an environment "
                    "execution test, not the final "
                    "legal analysis."
                ),
                "style": "Body Text",
            },
        ),
        ActionRequest(
            name="document.add_table",
            arguments={
                "path": (
                    "work/"
                    "research-charge-review-memo.odt"
                ),
                "headers": [
                    "Issue",
                    "Guideline",
                    "Adjustment",
                ],
                "rows": [
                    [
                        "Westlaw disbursement",
                        "Section 9.3",
                        "$4,850.00",
                    ]
                ],
            },
        ),
        ActionRequest(
            name="document.save_as",
            arguments={
                "source_path": (
                    "work/"
                    "research-charge-review-memo.odt"
                ),
                "target_path": (
                    "outputs/"
                    "research-charge-review-memo.docx"
                ),
            },
        ),
        ActionRequest(
            name="document.extract_text",
            arguments={
                "path": (
                    "outputs/"
                    "research-charge-review-memo.docx"
                ),
            },
        ),
    ]

    status = "passed"
    failure: dict | None = None

    try:
        environment.prepare(
            task,
            run_dir,
        )

        initial_snapshot = (
            environment.snapshot()
        )

        observation = environment.observe()
        recorder.append(
            "observation",
            observation,
        )
        action_queue = list(actions)
        executed_action_count = 0

        while action_queue:
            request = action_queue.pop(0)   
            result = environment.act(
                request
            )

            recorder.append(
                "action_result",
                result,
            )

            executed_action_count += 1

            if request.name == "email.search":
                messages = result.output.get(
                    "messages",
                    []
                )

                if len(messages) != 1:
                    status = "failed"
                    failure = {
                        "action": request.name,
                        "error": (
                            "expected_exactly_one_email"
                        ),
                        "message_count": len(messages),
                    }
                    break

                action_queue.insert(
                    0,
                    ActionRequest(
                        name="email.read",
                        arguments={
                            "message_id": (
                                messages[0][
                                    "message_id"
                                ]
                            )
                        },
                    ),
                )

            observation = (
                environment.observe()
            )

            recorder.append(
                "observation",
                observation,
            )

        final_snapshot = environment.snapshot()

        output_dir = (
            run_dir
            / "workspace"
            / "outputs"
        )
        artifact_dir = (
            run_dir / "artifacts"
        )

        if output_dir.exists():
            shutil.copytree(
                output_dir,
                artifact_dir,
                dirs_exist_ok=True,
            )

        final_docx = (
            artifact_dir
            / "research-charge-review-memo.docx"
        )
        independent_verification = (
            verify_docx_independently(
                final_docx
            )
        )

        (
            run_dir / "verification.json"
        ).write_text(
            json.dumps(
                independent_verification,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        recorder.append(
            "independent_verification",
            independent_verification,
        )

        if not independent_verification[
            "passed"
        ]:
            status = "failed"
            failure = {
                "error": (
                    "independent_docx_"
                    "verification_failed"
                )
            }

        environment.reset()

        reset_snapshot = (
            environment.snapshot()
        )

        reset_passed = (
            reset_snapshot["digest"]
            == initial_snapshot["digest"]
        )

        recorder.append(
            "reset",
            {
                "passed": reset_passed,
                "initial_digest": (
                    initial_snapshot[
                        "digest"
                    ]
                ),
                "reset_digest": (
                    reset_snapshot[
                        "digest"
                    ]
                ),
            },
        )

        if not reset_passed:
            status = "failed"
            failure = {
                "error": (
                    "reset_digest_mismatch"
                )
            }

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task.id,
            "environment_id": (
                environment_spec.id
            ),
            "status": status,
            "failure": failure,
            "action_count": executed_action_count,
            "initial_snapshot": (
                initial_snapshot
            ),
            "final_snapshot": (
                final_snapshot
            ),
            "reset_snapshot": (
                reset_snapshot
            ),
            "files": {
                "events": "events.jsonl",
                "trajectory": (
                    "trajectory.jsonl"
                ),
                "audit": (
                    "audit/actions.jsonl"
                ),
                "artifacts": "artifacts/",
                "verification": (
                    "verification.json"
                ),
            },
            "independent_verification": (
                independent_verification
            ),
        }

        (
            run_dir / "manifest.json"
        ).write_text(
            json.dumps(
                to_jsonable(manifest),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    except Exception as exc:
        status = "failed"

        (
            run_dir / "failure.json"
        ).write_text(
            json.dumps(
                {
                    "type": (
                        type(exc).__name__
                    ),
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            (
                "Inspector run failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
            file=sys.stderr,
            flush=True,
        )

    finally:
        environment.close()

    print(
        "\nInspector output:",
        run_dir,
        flush=True,
    )
    print(
        "Events:",
        run_dir / "events.jsonl",
        flush=True,
    )
    print(
        "Trajectory:",
        run_dir / "trajectory.jsonl",
        flush=True,
    )
    print(
        "Audit:",
        (
            run_dir
            / "audit"
            / "actions.jsonl"
        ),
        flush=True,
    )
    print(
        "Artifacts:",
        run_dir / "artifacts",
        flush=True,
    )
    print(
        "Verification:",
        run_dir / "verification.json",
        flush=True,
    )

    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
