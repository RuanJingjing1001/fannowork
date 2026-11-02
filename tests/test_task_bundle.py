from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.tasking import (
    TaskBundleError,
    TaskBundleResolver,
    validate_task_bundle,
)


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
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


class TaskBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.task_dir = self.root / "task"
        self.environment_dir = (
            self.root / "environment"
        )

        asset_path = (
            self.task_dir
            / "assets"
            / "contract.txt"
        )
        asset_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        asset_path.write_text(
            "contract fixture\n",
            encoding="utf-8",
        )

        email_path = (
            self.task_dir
            / "assets"
            / "source.eml"
        )
        email_path.write_text(
            "Subject: Test\n\nBody\n",
            encoding="utf-8",
        )

        write_json(
            self.task_dir / "seed.json",
            {
                "schema_version": 1,
                "collections": [],
                "email": {
                    "messages": [
                        {
                            "source": {
                                "path": (
                                    "assets/source.eml"
                                )
                            }
                        }
                    ]
                },
            },
        )

        write_json(
            self.task_dir / "provenance.json",
            {
                "schema_version": 1,
                "package_id": "test.bundle.v1",
                "materials": [
                    {
                        "imported_path": (
                            "assets/contract.txt"
                        )
                    }
                ],
            },
        )

        write_json(
            self.task_dir / "task.json",
            {
                "schema_version": 1,
                "id": "test.bundle.v1",
                "title": "Bundle test",
                "instruction": "Review the contract.",
                "environment_id": (
                    "test.environment.v1"
                ),
                "inputs": [
                    {
                        "path": "contract.txt",
                        "source": (
                            "assets/contract.txt"
                        ),
                        "media_type": "text/plain",
                    }
                ],
                "outputs": [],
                "verifier": {
                    "adapter": "required_artifacts",
                    "config": {
                        "status": "temporary"
                    },
                },
                "initial_state_file": "seed.json",
                "metadata": {
                    "provenance_file": (
                        "provenance.json"
                    )
                },
            },
        )

        write_json(
            self.environment_dir / "actions.json",
            {
                "schema_version": 1,
                "actions": [
                    {
                        "id": "filesystem.read",
                        "description": "Read a file",
                        "input_schema": {
                            "type": "object"
                        },
                    }
                ],
            },
        )

        write_json(
            self.environment_dir
            / "environment.json",
            {
                "schema_version": 1,
                "id": "test.environment.v1",
                "adapter": "workspace",
                "version": "1.0.0",
                "config": {
                    "actions_file": "actions.json",
                    "runtime_image": (
                        "test-environment:1.0"
                    ),
                },
            },
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(
        self,
        name: str,
    ):
        return TaskBundleResolver().build(
            task_path=(
                self.task_dir / "task.json"
            ),
            environment_path=(
                self.environment_dir
                / "environment.json"
            ),
            output_dir=self.root / name,
        )

    def test_builds_complete_bundle(self) -> None:
        bundle = self.build("bundle-a")

        self.assertEqual(
            bundle.manifest.bundle_id,
            "test.bundle.v1",
        )
        self.assertTrue(
            (
                bundle.root
                / "assets"
                / "contract.txt"
            ).is_file()
        )
        self.assertTrue(
            (
                bundle.root
                / "assets"
                / "source.eml"
            ).is_file()
        )
        self.assertEqual(
            bundle.environment["config"][
                "actions_file"
            ],
            "actions.json",
        )

        manifest = validate_task_bundle(
            bundle.root
        )
        self.assertEqual(
            manifest.bundle_digest,
            bundle.manifest.bundle_digest,
        )

    def test_build_is_deterministic(self) -> None:
        first = self.build("bundle-a")
        second = self.build("bundle-b")

        self.assertEqual(
            first.manifest.bundle_digest,
            second.manifest.bundle_digest,
        )
        self.assertEqual(
            first.manifest.to_dict(),
            second.manifest.to_dict(),
        )

    def test_validation_detects_tampering(
        self,
    ) -> None:
        bundle = self.build("bundle-a")
        (
            bundle.root
            / "assets"
            / "contract.txt"
        ).write_text(
            "tampered\n",
            encoding="utf-8",
        )

        with self.assertRaises(
            TaskBundleError
        ):
            validate_task_bundle(bundle.root)

    def test_validation_rejects_unregistered_file(
        self,
    ) -> None:
        bundle = self.build("bundle-a")
        (bundle.root / "unexpected.txt").write_text(
            "not in manifest\n",
            encoding="utf-8",
        )

        with self.assertRaises(
            TaskBundleError
        ):
            validate_task_bundle(bundle.root)

    def test_existing_output_requires_force(
        self,
    ) -> None:
        self.build("bundle-a")

        with self.assertRaises(
            TaskBundleError
        ):
            self.build("bundle-a")

    def test_force_refuses_non_bundle_directory(
        self,
    ) -> None:
        output = self.root / "not-a-bundle"
        output.mkdir()
        (output / "important.txt").write_text(
            "keep me\n",
            encoding="utf-8",
        )

        with self.assertRaises(
            TaskBundleError
        ):
            TaskBundleResolver().build(
                task_path=(
                    self.task_dir / "task.json"
                ),
                environment_path=(
                    self.environment_dir
                    / "environment.json"
                ),
                output_dir=output,
                force=True,
            )

        self.assertEqual(
            (output / "important.txt").read_text(
                encoding="utf-8"
            ),
            "keep me\n",
        )


if __name__ == "__main__":
    unittest.main()
