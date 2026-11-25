"""Cooking Session aggregate: session, its snapshot ingredients/steps, change log, timers, events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.db import Base


class CookingSessionORM(Base):
    __tablename__ = "cooking_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"))
    recipe_title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))
    current_step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Optimistic-lock counter; bumped with compare-and-swap on every state mutation.
    version: Mapped[int] = mapped_column(Integer, default=1)
    servings: Mapped[float | None] = mapped_column(nullable=True)
    base_servings: Mapped[float | None] = mapped_column(nullable=True)
    # Conversational pending confirmation, e.g. {"kind": "APPLY_CHANGE", "changeId": "..."}.
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optimistic locking: every UPDATE carries "WHERE version = <loaded version>".
    # version_id_generator=False → the service bumps the number explicitly on real state changes.
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    ingredients: Mapped[list[CookingSessionIngredientORM]] = relationship(
        back_populates="session", order_by="CookingSessionIngredientORM.position", cascade="all, delete-orphan"
    )
    steps: Mapped[list[CookingSessionStepORM]] = relationship(
        back_populates="session", order_by="CookingSessionStepORM.position", cascade="all, delete-orphan"
    )
    changes: Mapped[list[CookingSessionChangeORM]] = relationship(
        back_populates="session", order_by="CookingSessionChangeORM.created_at", cascade="all, delete-orphan"
    )


class CookingSessionIngredientORM(Base):
    __tablename__ = "cooking_session_ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("cooking_sessions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    canonical: Mapped[str] = mapped_column(String(100))
    raw_text: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(nullable=True)
    quantity_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # ACTIVE | EXCLUDED | REPLACED | ADDED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    substitute_for_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="RECIPE")  # RECIPE | CHANGE

    session: Mapped[CookingSessionORM] = relationship(back_populates="ingredients")


class CookingSessionStepORM(Base):
    __tablename__ = "cooking_session_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("cooking_sessions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1-based
    original_text: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)  # current (possibly adapted) instruction
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    ingredient_refs: Mapped[list[str]] = mapped_column(JSON, default=list)  # canonical names
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PENDING | IN_PROGRESS | COMPLETED
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[CookingSessionORM] = relationship(back_populates="steps")


class CookingSessionChangeORM(Base):
    __tablename__ = "cooking_session_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("cooking_sessions.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # EXCLUDE | SUBSTITUTE | SCALE
    # PROPOSED | APPLIED | REJECTED | NOT_APPLICABLE | SUPERSEDED
    status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))  # RULE | LLM
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    base_version: Mapped[int] = mapped_column(Integer)
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[CookingSessionORM] = relationship(back_populates="changes")


class TimerORM(Base):
    __tablename__ = "timers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("cooking_sessions.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # source of truth
    status: Mapped[str] = mapped_column(String(20))  # RUNNING | FINISHED | CANCELLED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionEventORM(Base):
    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("cooking_sessions.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyRecordORM(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    endpoint: Mapped[str] = mapped_column(String(100))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
