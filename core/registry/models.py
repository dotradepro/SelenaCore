"""
core/registry/models.py — SQLAlchemy ORM модели для Device Registry
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
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
