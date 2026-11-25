from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from server.app.schemas.base import ApiModel


class TimerStatus(StrEnum):
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class TimerCreateRequest(ApiModel):
    duration_seconds: int = Field(gt=0, le=6 * 3600)
    label: str | None = Field(default=None, max_length=100)


class TimerExtendRequest(ApiModel):
    seconds: int = Field(gt=0, le=3 * 3600)


class TimerOut(ApiModel):
    timer_id: str
    label: str
    status: TimerStatus
    duration_seconds: int
    ends_at: datetime
    remaining_seconds: int
    created_at: datetime


class SessionEventOut(ApiModel):
    event_id: int
    type: str
    message: str
    payload: dict[str, object]
    created_at: datetime


class EventsResponse(ApiModel):
    events: list[SessionEventOut]
    last_event_id: int
    timers: list[TimerOut]
