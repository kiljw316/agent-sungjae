from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from server.app.schemas.base import ApiModel
from server.app.schemas.change import ChangePreviewResponse
from server.app.schemas.session import CookingSessionOut, SessionSummary
from server.app.schemas.timer import TimerOut


class Intent(StrEnum):
    START_COOKING = "START_COOKING"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    END_SESSION = "END_SESSION"
    NEXT_STEP = "NEXT_STEP"
    REPEAT_STEP = "REPEAT_STEP"
    PREVIOUS_STEP_INFO = "PREVIOUS_STEP_INFO"
    GO_PREVIOUS = "GO_PREVIOUS"
    CURRENT_STEP_NUMBER = "CURRENT_STEP_NUMBER"
    REMAINING_STEPS = "REMAINING_STEPS"
    EXCLUDE_INGREDIENT = "EXCLUDE_INGREDIENT"
    KEEP_INGREDIENT = "KEEP_INGREDIENT"
    SUBSTITUTE_INGREDIENT = "SUBSTITUTE_INGREDIENT"
    SCALE_SERVINGS = "SCALE_SERVINGS"
    LIST_INGREDIENTS = "LIST_INGREDIENTS"
    COOKING_QUESTION = "COOKING_QUESTION"
    TIMER_START = "TIMER_START"
    TIMER_ADD = "TIMER_ADD"
    TIMER_STATUS = "TIMER_STATUS"
    TIMER_CANCEL = "TIMER_CANCEL"
    CONFIRM = "CONFIRM"
    DENY = "DENY"
    UNKNOWN = "UNKNOWN"


class CommandRequest(ApiModel):
    text: str = Field(min_length=1, max_length=500)
    expected_version: int | None = Field(default=None, ge=1)


class CommandResponse(ApiModel):
    intent: Intent
    speech: str
    handled_by: str
    requires_confirmation: bool = False
    state_changed: bool = False
    change: ChangePreviewResponse | None = None
    timer: TimerOut | None = None
    summary: SessionSummary | None = None
    session: CookingSessionOut
