"""system_modules/satellite_manager/__init__.py

`module_class` is lazy-imported via PEP 562 __getattr__ so that subpackage
imports (e.g. tests pulling in protocol.py alone) don't pull in the full
module — which requires Python 3.10+ for SQLAlchemy mapped types.
"""

__all__ = ["module_class"]


def __getattr__(name: str):
    if name == "module_class":
        from .module import SatelliteManagerModule
        return SatelliteManagerModule
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
