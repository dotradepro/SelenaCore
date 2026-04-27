"""Audio bridge: relay satellite PCM onto the EventBus for voice-core."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.eventbus.bus import EventBus

logger = logging.getLogger(__name__)


class AudioBridge:
    """Publishes satellite.* events that voice-core consumes.

    Kept as a stateless thin wrapper around the bus so WSHub can mock/swap
    it in tests without touching the real bus.
    """

    def __init__(self, bus: "EventBus", source: str = "satellite-manager") -> None:
        self._bus = bus
        self._source = source

    async def on_wake(self, session_id: str, device_id: str, location: str | None) -> None:
        await self._bus.publish(
            type="satellite.wake",
            source=self._source,
            payload={
                "session_id": session_id,
                "device_id": device_id,
                "location": location,
            },
        )

    async def on_audio_chunk(self, session_id: str, pcm_data: bytes) -> None:
        await self._bus.publish(
            type="satellite.audio_chunk",
            source=self._source,
            payload={"session_id": session_id, "pcm_data": pcm_data},
        )

    async def on_audio_end(self, session_id: str) -> None:
        await self._bus.publish(
            type="satellite.audio_end",
            source=self._source,
            payload={"session_id": session_id},
        )
