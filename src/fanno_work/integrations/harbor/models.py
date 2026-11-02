from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarborCompileConfig:
    schema_version: str = "1.4"
    task_version: str = "1.0.0"
    agent_timeout_sec: float = 1800.0
    verifier_timeout_sec: float = 300.0
    build_timeout_sec: float = 1200.0
    cpus: float = 2.0
    memory_mb: int = 2048
    storage_mb: int = 10240
    network_mode: str = "no-network"

    def __post_init__(self) -> None:
        if self.network_mode not in {
            "public",
            "no-network",
            "allowlist",
        }:
            raise ValueError(
                "network_mode must be public, "
                "no-network, or allowlist"
            )

        if self.agent_timeout_sec <= 0:
            raise ValueError(
                "agent_timeout_sec must be positive"
            )

        if self.verifier_timeout_sec <= 0:
            raise ValueError(
                "verifier_timeout_sec must be positive"
            )

        if self.build_timeout_sec <= 0:
            raise ValueError(
                "build_timeout_sec must be positive"
            )

        if self.cpus <= 0:
            raise ValueError(
                "cpus must be positive"
            )

        if self.memory_mb <= 0:
            raise ValueError(
                "memory_mb must be positive"
            )

        if self.storage_mb <= 0:
            raise ValueError(
                "storage_mb must be positive"
            )


@dataclass(frozen=True)
class HarborTaskPackage:
    root: Path
    task_name: str
    source_bundle_id: str
    source_bundle_digest: str
    package_digest: str