from fanno_work.registry import (
    PluginRegistry,
)

from fanno_legal.runtime.observed_environment import (
    ObservedLegalOfficeEnvironment,
)


def register_legal_plugin(
    registry: PluginRegistry,
) -> None:
    registry.register_environment(
        "legal_office",
        ObservedLegalOfficeEnvironment,
    )