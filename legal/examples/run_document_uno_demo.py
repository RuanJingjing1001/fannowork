from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from fanno_work.agents import ScriptedAgent
from fanno_work.models import (
    ActionRequest,
)
from fanno_work.registry import default_registry
from fanno_work.runtime import WorkflowRunner
from fanno_work.specs import load_environment, load_task

from fanno_legal.state.audit import AuditLog
from fanno_legal.plugin import register_legal_plugin


APPENDED_TEXT = (
    "No execution is authorized until the legal review "
    "has been completed and documented."
)


def uno_is_available() -> bool:
    """Return whether this process can launch the PyUNO worker."""
    python = Path("/usr/bin/python3")

    if not python.is_file():
        return False

    try:
        completed = subprocess.run(
            [str(python), "-c", "import uno"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return completed.returncode == 0


def extract_docx_text(path: Path) -> str:
    """Independently inspect DOCX XML; do not call LibreOffice."""
    namespace = (
        "{http://schemas.openxmlformats.org/"
        "wordprocessingml/2006/main}"
    )

    with ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    paragraphs: list[str] = []

    for paragraph in root.iter(f"{namespace}p"):
        value = "".join(
            node.text or ""
            for node in paragraph.iter(f"{namespace}t")
        )
        if value:
            paragraphs.append(value)

    return "\n".join(paragraphs)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_action_results(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "action_result":
            results.append(event["payload"])

    return results


def _write_report(
    *,
    root: Path,
    manifest: dict[str, Any],
    source_text: str,
    edited_text: str,
    source_hash_before: str,
    source_hash_after: str,
    action_results: list[dict[str, Any]],
) -> None:
    artifacts = root / "run" / "artifacts"
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "action_count": manifest["action_count"],
        "source_unchanged": source_hash_before == source_hash_after,
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "appended_text_found": APPENDED_TEXT in edited_text,
        "reset_passed": manifest["reset"]["passed"],
        "files": {
            "source_docx": "task-assets/assets/source_contract.docx",
            "edited_docx": "run/artifacts/edited_contract.docx",
            "original_pdf": "run/artifacts/original_contract.pdf",
            "edited_pdf": "run/artifacts/edited_contract.pdf",
            "audit": "run/audit/actions.jsonl",
            "trajectory": "run/trajectory.jsonl",
            "manifest": "run/run.json",
        },
    }

    (root / "VERIFICATION.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (root / "ACTION_RESULTS.json").write_text(
        json.dumps(
            action_results,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    before = html.escape(source_text)
    after = html.escape(edited_text)
    status = html.escape(str(manifest["status"]))

    report = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LibreOffice UNO document demo</title>
  <style>
    body {{ font-family: sans-serif; margin: 2rem; color: #222; }}
    .ok {{ color: #087f23; font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
    .card {{ border: 1px solid #ccc; border-radius: 8px; padding: 1rem; }}
    object {{ width: 100%; height: 640px; border: 1px solid #ddd; }}
    pre {{ white-space: pre-wrap; background: #f6f8fa; padding: 1rem; }}
    code {{ background: #f6f8fa; padding: .1rem .3rem; }}
  </style>
</head>
<body>
  <h1>LibreOffice UNO end-to-end demonstration</h1>
  <p>Status: <span class="ok">{status}</span></p>
  <p>The input hash is unchanged: <strong>{source_hash_before == source_hash_after}</strong>.</p>
  <p>The appended paragraph was independently found in the DOCX: <strong>{APPENDED_TEXT in edited_text}</strong>.</p>
  <p>Environment reset passed: <strong>{manifest['reset']['passed']}</strong>.</p>
  <p>
    <a href="task-assets/assets/source_contract.docx">Open original DOCX</a> ·
    <a href="run/artifacts/edited_contract.docx">Open edited DOCX</a> ·
    <a href="run/audit/actions.jsonl">Open audit log</a> ·
    <a href="run/trajectory.jsonl">Open trajectory</a>
  </p>
  <div class="grid">
    <section class="card">
      <h2>Before: PDF rendered by LibreOffice</h2>
      <object data="run/artifacts/original_contract.pdf" type="application/pdf">
        <a href="run/artifacts/original_contract.pdf">Open original PDF</a>
      </object>
      <pre>{before}</pre>
    </section>
    <section class="card">
      <h2>After: PDF rendered by LibreOffice</h2>
      <object data="run/artifacts/edited_contract.pdf" type="application/pdf">
        <a href="run/artifacts/edited_contract.pdf">Open edited PDF</a>
      </object>
      <pre>{after}</pre>
    </section>
  </div>
</body>
</html>
"""

    (root / "REPORT.html").write_text(report, encoding="utf-8")

    markdown = f"""# LibreOffice UNO end-to-end demonstration

- Status: `{manifest['status']}`
- Input unchanged: `{source_hash_before == source_hash_after}`
- Appended text independently verified: `{APPENDED_TEXT in edited_text}`
- Environment reset passed: `{manifest['reset']['passed']}`
- Edited DOCX: `run/artifacts/edited_contract.docx`
- Before PDF: `run/artifacts/original_contract.pdf`
- After PDF: `run/artifacts/edited_contract.pdf`
- Visual report: `REPORT.html`

## Before

```text
{source_text}
```

## After

```text
{edited_text}
```
"""
    (root / "REPORT.md").write_text(markdown, encoding="utf-8")


def run_demo(
    *,
    environment_path: Path,
    task_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the complete environment path and preserve inspectable output."""
    environment_path = Path(environment_path).resolve()
    task_path = Path(task_path).resolve()
    root = Path(output_dir).resolve()

    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"output directory must be empty: {root}")

    root.mkdir(parents=True, exist_ok=True)
    task_assets = root / "task-assets"
    shutil.copytree(task_path.parent, task_assets)

    task = load_task(task_assets / task_path.name)
    if len(task.inputs) != 1:
        raise RuntimeError("the document demo requires exactly one input")
    source = (task.source_dir / task.inputs[0].source).resolve()

    source_hash_before = _sha256(source)
    environment_spec = load_environment(environment_path)

    actions = [
        ActionRequest(
            "document.convert",
            {
                "source_path": "inputs/source_contract.docx",
                "target_path": "outputs/original_contract.pdf",
                "format": "pdf",
            },
        ),
        ActionRequest(
            "document.append_paragraph",
            {
                "source_path": "inputs/source_contract.docx",
                "target_path": "outputs/edited_contract.docx",
                "text": APPENDED_TEXT,
            },
        ),
        ActionRequest(
            "document.extract_text",
            {"path": "outputs/edited_contract.docx"},
        ),
        ActionRequest(
            "document.convert",
            {
                "source_path": "outputs/edited_contract.docx",
                "target_path": "outputs/edited_contract.pdf",
                "format": "pdf",
            },
        ),
    ]

    registry = default_registry()
    register_legal_plugin(registry)
    run_dir = root / "run"
    manifest = WorkflowRunner(registry).run(
        task=task,
        environment_spec=environment_spec,
        agent=ScriptedAgent(actions),
        run_dir=run_dir,
    )

    source_hash_after = _sha256(source)
    edited_docx = run_dir / "artifacts" / "edited_contract.docx"
    original_pdf = run_dir / "artifacts" / "original_contract.pdf"
    edited_pdf = run_dir / "artifacts" / "edited_contract.pdf"

    if manifest["status"] != "passed":
        raise RuntimeError(f"demo run failed: {manifest['execution_error']}")
    for path in (edited_docx, original_pdf, edited_pdf):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"expected artifact is missing: {path}")

    source_text = extract_docx_text(source)
    edited_text = extract_docx_text(edited_docx)

    if source_hash_before != source_hash_after:
        raise RuntimeError("the protected source document changed")
    if APPENDED_TEXT not in edited_text:
        raise RuntimeError("the appended paragraph was not found")

    action_results = _load_action_results(run_dir / "trajectory.jsonl")
    audit_events = AuditLog(
        run_dir / "audit" / "actions.jsonl"
    ).read_all()
    append_events = [
        event
        for event in audit_events
        if event["action"] == "document.append_paragraph"
    ]
    if len(append_events) != 1:
        raise RuntimeError("append action audit event is missing")
    if "text" in append_events[0]["arguments"]:
        raise RuntimeError("audit log contains protected paragraph text")

    _write_report(
        root=root,
        manifest=manifest,
        source_text=source_text,
        edited_text=edited_text,
        source_hash_before=source_hash_before,
        source_hash_after=source_hash_after,
        action_results=action_results,
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run an inspectable LibreOffice UNO document-editing demo."
        )
    )
    parser.add_argument(
        "--environment",
        type=Path,
        default=Path(
            "legal/environments/legal-office/v1/environment.json"
        ),
    )
    parser.add_argument(
        "--task",
        type=Path,
        default=Path(
            "legal/tasks/examples/document-append/v1/task.json"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = run_demo(
        environment_path=args.environment,
        task_path=args.task,
        output_dir=args.output_dir,
    )
    output_dir = args.output_dir.resolve()
    print(f"status={manifest['status']}")
    print(f"visual_report={output_dir / 'REPORT.html'}")
    print(
        "edited_docx="
        f"{output_dir / 'run' / 'artifacts' / 'edited_contract.docx'}"
    )
    print(
        "before_pdf="
        f"{output_dir / 'run' / 'artifacts' / 'original_contract.pdf'}"
    )
    print(
        "after_pdf="
        f"{output_dir / 'run' / 'artifacts' / 'edited_contract.pdf'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

