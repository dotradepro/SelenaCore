"""
core/registry/models.py — SQLAlchemy ORM models for Device Registry
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # sensor|actuator|controller|virtual
    protocol: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    capabilities: Mapped[str] = mapped_column(Text, default="[]")  # JSON string
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    module_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)   # e.g. "light", "thermostat", "speaker"
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)     # e.g. "kitchen", "bedroom", "living_room"
    keywords_user: Mapped[str] = mapped_column(Text, default="[]")  # JSON: user-entered keywords in any language
    keywords_en: Mapped[str] = mapped_column(Text, default="[]")    # JSON: auto-translated EN keywords for matching
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    state_history: Mapped[list[StateHistory]] = relationship(
        "StateHistory", back_populates="device", cascade="all, delete-orphan"
    )

    def get_state(self) -> dict:
        return json.loads(self.state)

    def set_state(self, state: dict) -> None:
        self.state = json.dumps(state)

    def get_capabilities(self) -> list[str]:
        return json.loads(self.capabilities)

    def set_capabilities(self, caps: list[str]) -> None:
        self.capabilities = json.dumps(caps)

    def get_meta(self) -> dict:
        return json.loads(self.meta)

    def set_meta(self, meta: dict) -> None:
        self.meta = json.dumps(meta)

    def get_keywords_user(self) -> list[str]:
        return json.loads(self.keywords_user)

    def set_keywords_user(self, keywords: list[str]) -> None:
        self.keywords_user = json.dumps(keywords, ensure_ascii=False)

    def get_keywords_en(self) -> list[str]:
        return json.loads(self.keywords_en)

    def set_keywords_en(self, keywords: list[str]) -> None:
        self.keywords_en = json.dumps(keywords, ensure_ascii=False)


class StateHistory(Base):
    __tablename__ = "state_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False
    )
    old_state: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    new_state: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    device: Mapped[Device] = relationship("Device", back_populates="state_history")


class RegisteredModule(Base):
    """Persistent module catalog — survives core restarts."""
    __tablename__ = "registered_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)  # module name (key)
    name_user: Mapped[str] = mapped_column(String(255), default="")   # display name (any language)
    name_en: Mapped[str] = mapped_column(String(255), default="")     # auto-translated EN name
    description_user: Mapped[str] = mapped_column(Text, default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    intents: Mapped[str] = mapped_column(Text, default="[]")          # JSON array of intent names
    entities: Mapped[str] = mapped_column(Text, default="[]")         # JSON array of entity types
    group: Mapped[str] = mapped_column(String(50), default="")
    module_type: Mapped[str] = mapped_column(String(50), default="SYSTEM")  # SYSTEM|UI|INTEGRATION|...
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def get_intents(self) -> list[str]:
        return json.loads(self.intents)

    def set_intents(self, items: list[str]) -> None:
        self.intents = json.dumps(items)

    def get_entities(self) -> list[str]:
        return json.loads(self.entities)

    def set_entities(self, items: list[str]) -> None:
        self.entities = json.dumps(items)


class RadioStation(Base):
    """Radio station catalog — used by LLM prompt and media-player."""
    __tablename__ = "radio_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_user: Mapped[str] = mapped_column(String(255), nullable=False)  # original name (any language)
    name_en: Mapped[str] = mapped_column(String(255), default="")        # auto-translated EN
    stream_url: Mapped[str] = mapped_column(Text, nullable=False)
    genre_user: Mapped[str] = mapped_column(String(100), default="")
    genre_en: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(50), default="")
    logo_url: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    favourite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Scene(Base):
    """Scene definitions — named sets of device actions."""
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_user: Mapped[str] = mapped_column(String(255), nullable=False)  # display name (any language)
    name_en: Mapped[str] = mapped_column(String(255), default="")        # auto-translated EN
    actions: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{device_id, state}, ...]
    trigger: Mapped[str] = mapped_column(Text, default="")    # cron/event trigger (optional)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def get_actions(self) -> list[dict]:
        return json.loads(self.actions)

    def set_actions(self, items: list[dict]) -> None:
        self.actions = json.dumps(items)


class IntentDefinition(Base):
    """Intent definitions — replaces definitions.yaml."""
    __tablename__ = "intent_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # "media.play_genre"
    module: Mapped[str] = mapped_column(String(100), default="")  # "media-player"
    noun_class: Mapped[str] = mapped_column(String(50), default="")  # "MEDIA"
    verb: Mapped[str] = mapped_column(String(50), default="")  # "play"
    priority: Mapped[int] = mapped_column(Integer, default=5)  # higher = checked first
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="system")  # system|user|auto
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    params_schema: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    # JSON array of entity types this intent is allowed to act on, e.g.
    # '["air_conditioner","thermostat","radiator"]'. NULL / empty = no
    # restriction. Lets the router's device resolver narrow down candidates
    # without any hardcoded Python mapping.
    entity_types: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    patterns: Mapped[list[IntentPattern]] = relationship(
        "IntentPattern", back_populates="definition", cascade="all, delete-orphan"
    )

    def get_params_schema(self) -> dict:
        return json.loads(self.params_schema)

    def set_params_schema(self, schema: dict) -> None:
        self.params_schema = json.dumps(schema)

    def get_entity_types(self) -> list[str]:
        """Return the allowed entity types for this intent, or []."""
        if not self.entity_types:
            return []
        try:
            val = json.loads(self.entity_types)
            return [str(x) for x in val] if isinstance(val, list) else []
        except Exception:
            return []

    def set_entity_types(self, types: list[str] | None) -> None:
        self.entity_types = json.dumps(list(types)) if types else None


class IntentPattern(Base):
    """Compiled regex patterns per language for an intent."""
    __tablename__ = "intent_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("intent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    lang: Mapped[str] = mapped_column(String(5), nullable=False)  # "en", "uk"
    pattern: Mapped[str] = mapped_column(Text, nullable=False)  # raw regex string
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|template|auto_entity
    entity_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "radio_station:42"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    definition: Mapped[IntentDefinition] = relationship("IntentDefinition", back_populates="patterns")


class IntentVocab(Base):
    """Vocabulary for intent pattern expansion — replaces vocab/*.yaml."""
    __tablename__ = "intent_vocab"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lang: Mapped[str] = mapped_column(String(5), nullable=False)  # "en", "uk"
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # verb|noun|param|location
    key: Mapped[str] = mapped_column(String(50), nullable=False)  # "play", "radio", "genre"
    words: Mapped[str] = mapped_column(Text, default="[]")  # JSON array: ["play", "put on", "start"]
    stems: Mapped[str] = mapped_column(Text, default="[]")  # JSON array: ["увімк"] (morphological)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("lang", "category", "key", name="uq_intent_vocab_lang_cat_key"),
    )

    def get_words(self) -> list[str]:
        return json.loads(self.words)

    def set_words(self, items: list[str]) -> None:
        self.words = json.dumps(items, ensure_ascii=False)

    def get_stems(self) -> list[str]:
        return json.loads(self.stems)

    def set_stems(self, items: list[str]) -> None:
        self.stems = json.dumps(items, ensure_ascii=False)


class SystemPrompt(Base):
    """Stores LLM prompts per language. Seeded from JSON, editable via UI."""
    __tablename__ = "system_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lang: Mapped[str] = mapped_column(String(10), nullable=False)
    key: Mapped[str] = mapped_column(String(50), nullable=False)  # user_prompt, compact_user, classification_prompt, rephrase_prompt
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)  # True = user-edited or LLM-translated
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("lang", "key", name="uq_prompt_lang_key"),
    )


class DriverProvider(Base):
    """Per-installation driver-provider state.

    A *provider* is a smart-device protocol library (e.g. ``tinytuya``,
    ``greeclimate``, ``phue``). Each customer enables only the providers
    they actually use, persisted in this table so that container restarts
    and power loss don't lose the configuration. ``ProviderLoader`` reads
    this table on startup and dynamically populates the device-control
    DRIVERS dict — no eager imports.

    Lifecycle:
      * builtin providers (tinytuya, greeclimate) are auto-seeded with
        ``auto_detected=True, enabled=True`` on first startup if their
        Python package is importable.
      * user-installed providers are added via the Providers tab UI,
        which runs ``pip install`` then INSERTs the row only on success.
      * if a provider's package becomes un-importable later (broken
        site-packages), the loader writes the ImportError to
        ``last_error`` and skips it without crashing device-control.
    """
    __tablename__ = "driver_providers"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # catalog id, e.g. "gree"
    package: Mapped[str | None] = mapped_column(String(255), nullable=True)  # pip package name
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)   # version spec
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClockAlarm(Base):
    """Alarm clock entry — recurring or one-shot."""
    __tablename__ = "clock_alarms"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    label: Mapped[str] = mapped_column(String(255), default="")
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat_days: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[int] 0-6 (Mon-Sun)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    snooze_minutes: Mapped[int] = mapped_column(Integer, default=0)
    sound: Mapped[str] = mapped_column(String(100), default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def get_repeat_days(self) -> list[int]:
        return json.loads(self.repeat_days)

    def set_repeat_days(self, days: list[int]) -> None:
        self.repeat_days = json.dumps(days)


class ClockTimer(Base):
    """Countdown timer entry."""
    __tablename__ = "clock_timers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    label: Mapped[str] = mapped_column(String(255), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_remaining_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="running")  # running|paused|finished
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ClockReminder(Base):
    """One-shot reminder at an absolute datetime."""
    __tablename__ = "clock_reminders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    label: Mapped[str] = mapped_column(String(255), default="")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ClockWorldCity(Base):
    """World clock city entry."""
    __tablename__ = "clock_world_cities"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    tz_name: Mapped[str] = mapped_column(String(100), nullable=False)  # IANA tz, e.g. "Asia/Tokyo"
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ClockStopwatch(Base):
    """Singleton stopwatch state (single row, id=1)."""
    __tablename__ = "clock_stopwatch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    state: Mapped[str] = mapped_column(String(20), default="idle")  # idle|running|paused
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    laps: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[int] of lap milestones (ms)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def get_laps(self) -> list[int]:
        return json.loads(self.laps)

    def set_laps(self, items: list[int]) -> None:
        self.laps = json.dumps(items)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    details: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
