"""
core/module_registry.py — Module Registry

Central registry for module metadata: intents, entities, groups.
Used by IntentRouter and DeviceRegistry to resolve which module handles
a given intent or entity type.

Populated from manifest.json during module loading (sandbox.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModuleEntry:
    """Metadata for a registered module."""
    name: str                           # e.g. "media-player"
    group: str                          # e.g. "media"
    intents: list[str] = field(default_factory=list)   # e.g. ["media.play", "media.stop"]
    entities: list[str] = field(default_factory=list)   # e.g. ["radio", "music"]
    description: str = ""
    type: str = "SYSTEM"                # SYSTEM | UI | INTEGRATION | ...
    status: str = "READY"


class ModuleRegistry:
    """Central module metadata registry.

    Resolves:
      intent  → module name
      entity  → list of module names
      group   → list of module names
    """

    def __init__(self) -> None:
        self._modules: dict[str, ModuleEntry] = {}
        # Reverse indexes for fast lookup
        self._intent_to_module: dict[str, str] = {}
        self._entity_to_modules: dict[str, list[str]] = {}
        self._group_to_modules: dict[str, list[str]] = {}

    def register(self, entry: ModuleEntry) -> None:
        """Register or update a module entry."""
        self._modules[entry.name] = entry
        self._rebuild_indexes()
        logger.info(
            "ModuleRegistry: registered '%s' group=%s intents=%s entities=%s",
            entry.name, entry.group, entry.intents, entry.entities,
        )

    def unregister(self, name: str) -> None:
        """Remove a module from the registry."""
        if name in self._modules:
            del self._modules[name]
            self._rebuild_indexes()
            logger.info("ModuleRegistry: unregistered '%s'", name)

    def _rebuild_indexes(self) -> None:
        """Rebuild reverse-lookup indexes from module entries."""
        self._intent_to_module.clear()
        self._entity_to_modules.clear()
        self._group_to_modules.clear()

        for entry in self._modules.values():
            for intent in entry.intents:
                self._intent_to_module[intent] = entry.name

            for entity in entry.entities:
                self._entity_to_modules.setdefault(entity, [])
                if entry.name not in self._entity_to_modules[entity]:
                    self._entity_to_modules[entity].append(entry.name)

            self._group_to_modules.setdefault(entry.group, [])
            if entry.name not in self._group_to_modules[entry.group]:
                self._group_to_modules[entry.group].append(entry.name)

    # ── Query methods ────────────────────────────────────────────────

    def get_module_for_intent(self, intent: str) -> str | None:
        """Resolve an intent name to the module that handles it.

        Supports both exact match ("media.play") and prefix match ("media.play_genre").
        """
        # Exact match
        if intent in self._intent_to_module:
            return self._intent_to_module[intent]
        # Prefix match: "media.play_genre" matches registered "media.play"
        for registered, module in self._intent_to_module.items():
            if intent.startswith(registered):
                return module
        return None

    def get_modules_for_entity(self, entity_type: str) -> list[str]:
        """Get all modules that handle a given entity type."""
        return list(self._entity_to_modules.get(entity_type, []))

    def get_modules_for_group(self, group: str) -> list[str]:
        """Get all modules in a given group."""
        return list(self._group_to_modules.get(group, []))

    def get_entry(self, name: str) -> ModuleEntry | None:
        """Get the registry entry for a module."""
        return self._modules.get(name)

    def list_entries(self) -> list[ModuleEntry]:
        """List all registered module entries."""
        return list(self._modules.values())

    def resolve(
        self,
        intent: str | None = None,
        entity: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve matching modules for a given intent/entity/location.

        Returns list of dicts: [{"module": str, "confidence": float}]
        sorted by confidence descending.
        """
        candidates: dict[str, float] = {}

        # Intent match — highest confidence
        if intent:
            module = self.get_module_for_intent(intent)
            if module:
                candidates[module] = candidates.get(module, 0) + 0.8

        # Entity match — medium confidence
        if entity:
            for module in self.get_modules_for_entity(entity):
                candidates[module] = candidates.get(module, 0) + 0.5

        # Location currently adds no confidence — it's used for device
        # disambiguation, not module resolution. Placeholder for future use.

        result = [
            {"module": module, "confidence": round(score, 2)}
            for module, score in sorted(candidates.items(), key=lambda x: -x[1])
        ]
        return result

    def get_all_intents(self) -> list[str]:
        """Get flat list of all registered intent names."""
        return list(self._intent_to_module.keys())

    def get_all_entities(self) -> list[str]:
        """Get flat list of all registered entity types."""
        return list(self._entity_to_modules.keys())

    def get_all_groups(self) -> set[str]:
        """Get set of all registered groups."""
        return set(self._group_to_modules.keys())


# ── Singleton ────────────────────────────────────────────────────────────

_registry: ModuleRegistry | None = None


def get_module_registry() -> ModuleRegistry:
    global _registry
    if _registry is None:
        _registry = ModuleRegistry()
    return _registry
