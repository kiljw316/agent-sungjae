from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from server.app.schemas.base import ApiModel


class SessionStatus(StrEnum):
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class IngredientStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXCLUDED = "EXCLUDED"
    REPLACED = "REPLACED"
    ADDED = "ADDED"


class SessionIngredientOut(ApiModel):
    id: str
    name: str
    canonical: str
    raw_text: str
    quantity: float | None
    quantity_text: str | None
    unit: str | None
    note: str | None
    status: IngredientStatus
    substitute_for_id: str | None
    origin: str
    display: str


class SessionStepOut(ApiModel):
    id: str
    position: int
    text: str
    original_text: str
    notes: list[str]
    ingredient_refs: list[str]
    duration_minutes: int | None
    status: StepStatus
    completed_at: datetime | None


class AppliedChangeOut(ApiModel):
    change_id: str
    type: str
    status: str
    source: str
    summary: str
    created_at: datetime
    applied_at: datetime | None


class CookingSessionOut(ApiModel):
    session_id: str
    recipe_id: str
    recipe_title: str
    status: SessionStatus
    current_step_id: str | None
    current_step_position: int | None
    total_steps: int
    version: int
    servings: float | None
    base_servings: float | None
    ingredients: list[SessionIngredientOut]
    steps: list[SessionStepOut]
    applied_changes: list[AppliedChangeOut]
    pending_action: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


class CreateSessionRequest(ApiModel):
    recipe_id: str
    servings: float | None = Field(default=None, gt=0, le=50)


class VersionedRequest(ApiModel):
    """Optimistic-lock guard: request fails with 409 if the session moved on."""

    expected_version: int | None = Field(default=None, ge=1)


class StepResponse(ApiModel):
    session: CookingSessionOut
    speech: str


class SessionSummary(ApiModel):
    session_id: str
    recipe_title: str
    status: SessionStatus
    completed_steps: int
    total_steps: int
    applied_changes: list[AppliedChangeOut]
    started_at: datetime | None
    ended_at: datetime | None
    speech: str


class SessionListItem(ApiModel):
    session_id: str
    recipe_title: str
    status: SessionStatus
    current_step_position: int | None
    total_steps: int
    updated_at: datetime
