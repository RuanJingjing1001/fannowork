from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .hashing import bundle_digest, sha256_file


@dataclass(frozen=True)
class ManifestFile:
    path: str
    sha256: str
    size_bytes: int
    role: str


@dataclass(frozen=True)
class ImageReference:
    reference: str
    digest: str | None = None
    provenance: str = "profile_reference"


@dataclass(frozen=True)
class BundleManifest:
    schema_version: int
    bundle_id: str
    task: dict[str, Any]
    environment: dict[str, Any]
    action_catalog: dict[str, Any]
    evaluator: dict[str, Any]
    files: tuple[ManifestFile, ...]
    images: tuple[ImageReference, ...]
    bundle_digest: str
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_manifest_files(
    bundle_root: Path,
    roles: dict[str, str],
) -> tuple[ManifestFile, ...]:
    result: list[ManifestFile] = []

    for path in sorted(bundle_root.rglob("*")):
        if not path.is_file():
            continue

        relative = path.relative_to(
            bundle_root
        ).as_posix()

        if relative == "manifest.json":
            continue

        result.append(
            ManifestFile(
                path=relative,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                role=roles.get(
                    relative,
                    "supporting_file",
                ),
            )
        )

    return tuple(result)


def calculate_manifest_digest(
    files: Iterable[ManifestFile],
) -> str:
    return bundle_digest(
        (item.path, item.sha256)
        for item in files
    )
