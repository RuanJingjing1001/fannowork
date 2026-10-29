from __future__ import annotations

from .environments.base import EnvironmentFactory, WorkflowEnvironment, build_environment
from .models import EnvironmentSpec, VerificationSpec
from .verifiers.base import Verifier, VerifierFactory, build_verifier


class PluginRegistry:
    def __init__(self) -> None:
        self._environments: dict[str, EnvironmentFactory] = {}
        self._verifiers: dict[str, VerifierFactory] = {}

    def register_environment(self, name: str, factory: EnvironmentFactory) -> None:
        if name in self._environments:
            raise ValueError(f"environment adapter already registered: {name}")
        self._environments[name] = factory

    def register_verifier(self, name: str, factory: VerifierFactory) -> None:
        if name in self._verifiers:
            raise ValueError(f"verifier adapter already registered: {name}")
        self._verifiers[name] = factory

    def create_environment(self, spec: EnvironmentSpec) -> WorkflowEnvironment:
        try:
            factory = self._environments[spec.adapter]
        except KeyError as exc:
            raise KeyError(f"unknown environment adapter: {spec.adapter}") from exc
        return build_environment(factory, spec)

    def create_verifier(self, spec: VerificationSpec) -> Verifier:
        try:
            factory = self._verifiers[spec.adapter]
        except KeyError as exc:
            raise KeyError(f"unknown verifier adapter: {spec.adapter}") from exc
        return build_verifier(factory, spec)

    def describe(self) -> dict[str, list[str]]:
        return {
            "environment_adapters": sorted(self._environments),
            "verifier_adapters": sorted(self._verifiers),
        }


def default_registry() -> PluginRegistry:
    from .environments.workspace import WorkspaceEnvironment
    from .verifiers.artifacts import RequiredArtifactsVerifier

    registry = PluginRegistry()
    registry.register_environment("workspace", WorkspaceEnvironment)
    registry.register_verifier("required_artifacts", RequiredArtifactsVerifier)
    return registry
