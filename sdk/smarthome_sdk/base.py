"""
sdk/smarthome_sdk/base.py — SmartHomeModule base class

Re-exports the full SmartHomeModule from sdk.base_module.
All SelenaCore user modules should inherit from this class.
"""
from sdk.base_module import SmartHomeModule  # noqa: F401

__all__ = ["SmartHomeModule"]
