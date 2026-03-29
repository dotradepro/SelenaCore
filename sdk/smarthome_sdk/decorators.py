"""
sdk/smarthome_sdk/decorators.py — Module decorators

@on_event("device.state_changed")  — subscribe to EventBus events
@scheduled("every:30s")            — run on interval
@intent("turn (on|off) the .*")    — register intent pattern
"""
from sdk.base_module import intent, on_event, scheduled  # noqa: F401

__all__ = ["intent", "on_event", "scheduled"]
