from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from server.app.schemas.base import ApiModel
from server.app.schemas.session import CookingSessionOut


class ChangeType(StrEnum):
    EXCLUDE = "EXCLUDE"
    SUBSTITUTE = "SUBSTITUTE"
    SCALE = "SCALE"


class ChangeStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SUPERSEDED = "SUPERSEDED"


class ChangePreviewRequest(ApiModel):
    type: ChangeType
    ingredient: str | None = Field(default=None, max_length=50)
    substitute: str | None = Field(default=None, max_length=50)
    servings: float | None = Field(default=None, gt=0, le=50)

    @model_validator(mode="after")
    def _check(self) -> ChangePreviewRequest:
        if self.type in (ChangeType.EXCLUDE, ChangeType.SUBSTITUTE) and not self.ingredient:
            raise ValueError("ingredient is required")
        if self.type is ChangeType.SUBSTITUTE and not self.substitute:
            raise ValueError("substitute is required")
        if self.type is ChangeType.SCALE and not self.servings:
            raise ValueError("servings is required")
        return self


class StepEdit(ApiModel):
    step_id: str
    position: int
    before: str
    after: str
    added_notes: list[str] = Field(default_factory=list)
    ingredient_refs: list[str] | None = None


class IngredientEdit(ApiModel):
    action: str  # EXCLUDE | REPLACE | ADD | RESCALE | ANNOTATE
    ingredient_id: str | None = None
    name: str
    before: str | None = None
    after: str | None = None
    quantity: float | None = None
    quantity_text: str | None = None
    unit: str | None = None
    note: str | None = None
    substitute_for_id: str | None = None


class ChangePlan(ApiModel):
    """Structured, server-validated change. Agents may only *propose* one of these."""

    applicable: bool
    reason: str | None = None
    ingredient_edits: list[IngredientEdit] = Field(default_factory=list)
    step_edits: list[StepEdit] = Field(default_factory=list)
    already_used_in_steps: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    new_servings: float | None = None
    scale_factor: float | None = None


class ChangePreviewResponse(ApiModel):
    change_id: str
    type: ChangeType
    status: ChangeStatus
    source: str
    summary: str
    requires_confirmation: bool
    plan: ChangePlan
    base_version: int


class ApplyChangeResponse(ApiModel):
    change_id: str
    status: ChangeStatus
    summary: str
    speech: str
    session: CookingSessionOut
