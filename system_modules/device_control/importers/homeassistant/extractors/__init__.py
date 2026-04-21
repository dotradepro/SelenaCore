"""Per-integration credential extractors.

An extractor takes one ``HADevice`` + optional user-provided session
context and returns an ``ExtractionResult`` describing whether the device
can be migrated to SelenaCore and, if so, what protocol + credentials to
use when creating the ``Device`` row.

Registration is explicit — unknown HA integrations are surfaced as
"unsupported" so the user sees them in the readiness report rather than
being silently dropped.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

from ..types import ExtractionResult, HADevice


class Extractor(Protocol):
    """Callable that converts one HA device into an ExtractionResult."""

    def __call__(
        self, device: HADevice, context: dict[str, Any] | None = None,
    ) -> ExtractionResult: ...


_REGISTRY: dict[str, Callable[[HADevice, dict[str, Any] | None], ExtractionResult]] = {}


def register(integration: str, extractor: Callable[[HADevice, dict[str, Any] | None], ExtractionResult]) -> None:
    """Bind an extractor to an HA integration id (e.g. "tuya", "esphome")."""
    _REGISTRY[integration] = extractor


def get(integration: str) -> Callable[[HADevice, dict[str, Any] | None], ExtractionResult] | None:
    return _REGISTRY.get(integration)


def known_integrations() -> list[str]:
    return sorted(_REGISTRY.keys())


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    """Run the extractor for ``device.integration`` or return unsupported."""
    fn = _REGISTRY.get(device.integration)
    if fn is None:
        return ExtractionResult(
            status="unsupported",
            reason=f"No importer for HA integration '{device.integration}'.",
        )
    return fn(device, context)


# Import submodules for their side effect of registering themselves.
from . import esphome  # noqa: F401, E402
from . import hue  # noqa: F401, E402
from . import mqtt  # noqa: F401, E402
from . import tuya  # noqa: F401, E402
from . import zigbee2mqtt  # noqa: F401, E402
from . import zwave_js  # noqa: F401, E402
