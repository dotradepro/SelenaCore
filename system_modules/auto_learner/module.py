"""
system_modules/auto_learner/module.py — AutoLearner system module.

Builds SmartMatcher index on startup, learns from LLM intent results,
runs nightly cleanup, and provides stats API.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dt_time
from typing import Any

from fastapi import APIRouter

from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)


class AutoLearnerModule(SystemModule):
    """System module: learns from LLM intent classifications to improve SmartMatcher."""

    name = "auto-learner"

    def __init__(self) -> None:
        super().__init__()
        self._learner: Any = None
        self._nightly_task: asyncio.Task | None = None
        self._rebuild_task: asyncio.Task | None = None

    async def start(self) -> None:
        # 1. Initialize IntentCompiler + SmartMatcher
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            from system_modules.llm_engine.smart_matcher import get_smart_matcher

            compiler = get_intent_compiler()
            matcher = get_smart_matcher()

            # Build TF-IDF index from compiled intents
            all_entries = []
            for module_name in compiler.get_all_modules():
                all_entries.extend(compiler.get_intents_for_module(module_name))
            all_definitions = compiler.get_all_definitions()

            matcher.build_index(all_entries, all_definitions)
            logger.info(
                "AutoLearner: SmartMatcher index built (%d entries)",
                matcher.entry_count,
            )

            # Start background rebuild loop (every 5 min if dirty)
            self._rebuild_task = asyncio.create_task(
                matcher.background_rebuild_loop(),
            )
        except Exception as exc:
            logger.warning("AutoLearner: SmartMatcher init failed: %s", exc)

        # 2. Initialize Learner
        from .learner import Learner

        self._learner = Learner()

        # Set known intents for validation
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            compiler = get_intent_compiler()
            known = []
            for defn in compiler.get_all_definitions():
                known.append(defn.name)
            self._learner.set_known_intents(known)
        except Exception:
            pass

        # 3. Subscribe to voice.intent events
        self.subscribe(["voice.intent"], self._on_voice_intent)

        # 4. Start nightly cleanup task
        self._nightly_task = asyncio.create_task(self._nightly_loop())

        await self.publish("module.started", {"name": self.name})
        logger.info("AutoLearner module started")

    async def stop(self) -> None:
        if self._nightly_task and not self._nightly_task.done():
            self._nightly_task.cancel()
        if self._rebuild_task and not self._rebuild_task.done():
            self._rebuild_task.cancel()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})
        logger.info("AutoLearner module stopped")

    def get_router(self) -> APIRouter | None:
        router = APIRouter()

        @router.get("/stats")
        async def stats() -> dict[str, Any]:
            """Return learner statistics."""
            if self._learner:
                return self._learner.get_stats()
            return {"total": 0, "confirmed": 0, "unconfirmed": 0}

        @router.get("/health")
        async def health() -> dict[str, Any]:
            from system_modules.llm_engine.smart_matcher import get_smart_matcher
            matcher = get_smart_matcher()
            return {
                "status": "ok",
                "module": self.name,
                "smart_matcher_built": matcher.is_built,
                "smart_matcher_entries": matcher.entry_count,
                "learner_stats": self._learner.get_stats() if self._learner else {},
            }

        return router

    # ── Event handlers ───────────────────────────────────────────────────

    async def _on_voice_intent(self, event: Any) -> None:
        """Handle voice.intent events — learn from LLM results."""
        if not self._learner:
            return

        payload = event.payload if hasattr(event, "payload") else {}

        learned = self._learner.on_voice_intent(payload)
        if learned:
            # Feed new example to SmartMatcher (rebuild is batched)
            try:
                from system_modules.llm_engine.smart_matcher import get_smart_matcher
                from system_modules.llm_engine.structure_extractor import extract_structure

                raw_text = payload.get("raw_text", "")
                intent = payload.get("intent", "")
                struct = extract_structure(raw_text)

                get_smart_matcher().add_example(raw_text, intent, {
                    "noun_class": struct.get("noun_class", "UNKNOWN"),
                    "verb": struct.get("verb", "UNKNOWN"),
                    "module": payload.get("module", ""),
                    "source": "llm",
                })
            except Exception as exc:
                logger.debug("AutoLearner: SmartMatcher add_example failed: %s", exc)

    # ── Nightly cleanup ──────────────────────────────────────────────────

    async def _nightly_loop(self) -> None:
        """Run cleanup at 03:00 daily."""
        while True:
            now = datetime.now()
            target = datetime.combine(now.date(), dt_time(3, 0))
            if now >= target:
                # Already past 3 AM today — schedule for tomorrow
                target = datetime.combine(
                    now.date() + __import__("datetime").timedelta(days=1),
                    dt_time(3, 0),
                )
            sleep_secs = (target - now).total_seconds()
            await asyncio.sleep(sleep_secs)

            try:
                if self._learner:
                    result = self._learner.nightly_cleanup()
                    logger.info("AutoLearner nightly cleanup: %s", result)
            except Exception as exc:
                logger.error("AutoLearner nightly cleanup error: %s", exc)
