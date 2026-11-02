"""Shared contracts for executable environment backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BackendErrorInfo:
    """Stable, serializable description of a backend failure."""

    code: str
    detail: str
    backend: str
    timed_out: bool = False
    retryable: bool = False


class BackendFailure(RuntimeError):
    """Base exception raised by all software/backend adapters."""

    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        backend: str = "unknown",
        timed_out: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.info = BackendErrorInfo(
            code=code,
            detail=detail,
            backend=backend,
            timed_out=timed_out,
            retryable=retryable,
        )

    @property
    def code(self) -> str:
        return self.info.code

    @property
    def detail(self) -> str:
        return self.info.detail

    def as_dict(self) -> dict[str, Any]:
        return asdict(self.info)


def execution_evidence(
    *,
    backend: str,
    execution_scope: str,
    timeout_seconds: int | None,
    runtime: str | None = None,
) -> dict[str, Any]:
    """Build the common evidence carried by backend observations."""

    result: dict[str, Any] = {
        "backend": backend,
        "execution_scope": execution_scope,
        "timeout_seconds": timeout_seconds,
    }
    if runtime is not None:
        result["runtime"] = runtime
    return result
