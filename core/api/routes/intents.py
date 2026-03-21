"""
core/api/routes/intents.py — Unified Module Intent Registry

Allows any module to declare voice intent patterns on startup.
IntentRouter queries this registry (Tier 2) before falling back to LLM.

Lifecycle (mirrors EventBus subscriptions — no DB needed):
  - Module starts → SDK auto-calls POST /api/v1/intents/register
  - IntentRouter: find_module_for_text(text, lang) → forward to module
  - Module uninstalled → DELETE /api/v1/intents/{module}
  - Core or module restarts → module re-registers itself on on_start()
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.api.auth import verify_module_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/intents", tags=["intents"])

# In-memory registry — modules re-register on restart (same pattern as EventBus)
# key: module_name -> ModuleIntentRecord
_intent_registry: dict[str, "_ModuleIntentRecord"] = {}


# ── Pydantic request/response models ─────────────────────────────────────────

class IntentPatterns(BaseModel):
    """Per-language regex/keyword patterns for an intent."""
    en: list[str] = Field(default_factory=list)
    uk: list[str] = Field(default_factory=list)
    ru: list[str] = Field(default_factory=list)


class IntentEntry(BaseModel):
    """A single intent declaration from a module."""
    patterns: IntentPatterns
    description: str = ""
    endpoint: str = "/api/intent"  # where Core should POST when matched


class IntentRegisterRequest(BaseModel):
    module: str = Field(..., min_length=1, max_length=100)
    port: int = Field(..., ge=8100, le=8300)
    intents: list[IntentEntry] = Field(..., min_length=1)


class IntentRegisterResponse(BaseModel):
    registered: bool
    module: str
    intent_count: int


# ── Internal record ───────────────────────────────────────────────────────────

class _ModuleIntentRecord:
    """Runtime registry entry for a module's intent declarations."""

    __slots__ = ("module", "port", "intents")

    def __init__(self, module: str, port: int, intents: list[IntentEntry]) -> None:
        self.module = module
        self.port = port
        self.intents = intents

    def find_endpoint(self, text: str, lang: str) -> str | None:
        """Return endpoint if any intent pattern matches text, else None.

        Falls back to 'en' patterns if the requested language has no patterns.
        """
        text_lower = text.lower()
        for entry in self.intents:
            lang_patterns: list[str] = getattr(entry.patterns, lang, []) or entry.patterns.en
            for pattern in lang_patterns:
                try:
                    if re.search(pattern, text_lower, re.IGNORECASE):
                        return entry.endpoint
                except re.error:
                    logger.warning("Invalid regex pattern '%s' in module '%s'", pattern, self.module)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "port": self.port,
            "intents": [
                {
                    "patterns": i.patterns.model_dump(exclude_none=True),
                    "description": i.description,
                    "endpoint": i.endpoint,
                }
                for i in self.intents
            ],
        }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=IntentRegisterResponse, status_code=201)
async def register_intents(
    body: IntentRegisterRequest,
    _token: str = Depends(verify_module_token),
) -> IntentRegisterResponse:
    """Module registers its voice intent patterns.

    Called automatically by SDK.start() for modules with @intent decorators.
    Re-registering the same module overwrites the previous record (idempotent).
    """
    record = _ModuleIntentRecord(
        module=body.module,
        port=body.port,
        intents=body.intents,
    )
    _intent_registry[body.module] = record
    logger.info(
        "Intent registry: '%s' registered %d intent(s) on port %d",
        body.module,
        len(body.intents),
        body.port,
    )
    return IntentRegisterResponse(
        registered=True,
        module=body.module,
        intent_count=len(body.intents),
    )


@router.get("")
async def list_intents(
    _token: str = Depends(verify_module_token),
) -> dict[str, Any]:
    """List all registered module intents.

    Primarily used by IntentRouter Tier 2 to discover module capabilities.
    """
    return {
        "modules": [r.to_dict() for r in _intent_registry.values()],
        "total": len(_intent_registry),
    }


@router.delete("/{module}", status_code=204, response_class=Response)
async def unregister_intents(
    module: str,
    _token: str = Depends(verify_module_token),
) -> Response:
    """Unregister all intents for a module (called on uninstall)."""
    if module not in _intent_registry:
        raise HTTPException(status_code=404, detail="Module not found in intent registry")
    del _intent_registry[module]
    logger.info("Intent registry: '%s' unregistered", module)
    return Response(status_code=204)


# ── Internal API (used by IntentRouter — no HTTP round-trip) ─────────────────

def find_module_for_text(text: str, lang: str = "en") -> tuple[str, int, str] | None:
    """Find the first registered module whose intent patterns match text.

    Returns (module_name, port, endpoint) or None if no module matches.
    Called directly by IntentRouter Tier 2 (same process, no HTTP overhead).
    """
    for record in _intent_registry.values():
        endpoint = record.find_endpoint(text, lang)
        if endpoint is not None:
            return record.module, record.port, endpoint
    return None
