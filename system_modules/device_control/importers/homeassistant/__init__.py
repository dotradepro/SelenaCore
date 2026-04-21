"""
Home Assistant one-time importer.

Contract: after ``/ha/import/run`` + a passing health check, the user may
power off and decommission Home Assistant — every imported device keeps
working because SelenaCore now talks to it directly through a native
driver (tuya_local, esphome, philips_hue, mqtt, …).

Flow:
    1. ``client.HAClient``      — authenticate over WebSocket with a LLAT
    2. ``fetcher.fetch_all``    — pull device/entity/area/entry registries
    3. ``extractors``           — per-integration credential extraction
    4. ``readiness.build``      — aggregate extractor results into green/
                                  yellow/red report for the UI
    5. ``runner.run``           — persist selected devices via the registry
    6. ``health_check.run``     — ping each new device through its target
                                  driver so rollback is one click away
"""

from .types import (
    ExtractionResult,
    ExtractionStatus,
    HADevice,
    HAEntity,
    ReadinessReport,
    ReadinessRow,
)

__all__ = [
    "ExtractionResult",
    "ExtractionStatus",
    "HADevice",
    "HAEntity",
    "ReadinessReport",
    "ReadinessRow",
]
