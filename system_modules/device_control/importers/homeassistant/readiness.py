"""Aggregate per-device extractor results into a readiness report.

The report is what the UI shows before any DB writes. Green rows will be
created, yellow rows need more input, red rows are informational (they
stay behind in HA).

The aggregator is deliberately dumb — it just tallies statuses and emits
a row per device. All the integration-specific logic lives in extractors.
"""
from __future__ import annotations

from typing import Any

from . import extractors
from .types import HADevice, ReadinessReport, ReadinessRow


def build(
    devices: list[HADevice],
    context: dict[str, Any] | None = None,
) -> ReadinessReport:
    """Run each extractor against its device and tally the results."""
    report = ReadinessReport()
    for device in devices:
        result = extractors.extract(device, context)
        row = ReadinessRow(
            ha_device_id=device.id,
            ha_device_name=device.name or f"({device.integration})",
            integration=device.integration,
            status=result.status,
            protocol=result.protocol,
            entity_type=result.entity_type,
            reason=result.reason,
            needs=list(result.needs),
        )
        report.rows.append(row)
        if result.status == "ok":
            report.green += 1
        elif result.status == "needs_user_input":
            report.yellow += 1
        else:
            report.red += 1
    return report


def aggregate_needs(report: ReadinessReport) -> list[str]:
    """Deduplicated list of yellow-row ``needs`` — the UI wizard renders
    one prompt per unique need, then re-calls preview with the extra
    context filled in."""
    seen: set[str] = set()
    out: list[str] = []
    for row in report.rows:
        if row.status != "needs_user_input":
            continue
        for n in row.needs:
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out
