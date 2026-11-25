from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.api.deps import Container, get_container
from server.app.clock import ensure_utc
from server.app.models import IdempotencyRecordORM
from server.app.repositories.session_repo import SessionRepository
from server.app.schemas.base import ErrorResponse
from server.app.schemas.change import (
    ApplyChangeResponse,
    ChangePreviewRequest,
    ChangePreviewResponse,
)
from server.app.schemas.command import CommandRequest, CommandResponse
from server.app.schemas.session import (
    CookingSessionOut,
    CreateSessionRequest,
    SessionListItem,
    SessionStatus,
    SessionSummary,
    StepResponse,
    VersionedRequest,
)
from server.app.schemas.timer import (
    EventsResponse,
    SessionEventOut,
    TimerCreateRequest,
    TimerExtendRequest,
    TimerOut,
)
from server.app.services import presenters
from server.app.services.change_service import (
    ChangeService,
    applied_speech,
    to_preview_response,
)
from server.app.services.command_service import CommandService
from server.app.services.session_service import SessionService
from server.app.services.timer_service import TimerService

router = APIRouter(prefix="/api/cooking-sessions", tags=["cooking-sessions"])
C = Annotated[Container, Depends(get_container)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]
ERRORS: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}


def _idempotent(
    db: Session, key: str | None, session_id: str, endpoint: str, fn: Callable[[], BaseModel]
) -> BaseModel | JSONResponse:
    """Replay the stored response for a repeated Idempotency-Key instead of re-executing."""
    if not key:
        return fn()
    repo = SessionRepository(db)
    scoped = f"{session_id}:{endpoint}:{key}"
    if (rec := repo.get_idempotent(scoped)) is not None:
        return JSONResponse(status_code=rec.status_code, content=rec.response, headers={"Idempotent-Replay": "true"})
    result = fn()
    repo.save_idempotent(
        IdempotencyRecordORM(
            key=scoped, session_id=session_id, endpoint=endpoint, status_code=200,
            response=result.model_dump(mode="json", by_alias=True), created_at=datetime.now(UTC),
        )
    )
    return result


def _expected(body: VersionedRequest | None) -> int | None:
    return body.expected_version if body else None


# ----------------------------------------------------------------- sessions
@router.post("", response_model=CookingSessionOut, status_code=status.HTTP_201_CREATED, responses=ERRORS)
def create_session(body: CreateSessionRequest, c: C) -> CookingSessionOut:
    """Create a session snapshot from a *confirmed* recipe. Status starts at READY."""
    with c.uow() as db:
        svc = SessionService(db, c.clock)
        s = svc.create(body.recipe_id)
        if body.servings and s.servings and body.servings != s.servings:
            change = ChangeService(db, c.clock, c.agent).preview(s, ChangePreviewRequest(type="SCALE", servings=body.servings))
            ChangeService(db, c.clock, c.agent).apply(s, change.id)
        db.flush()
        return presenters.to_session_out(s)


@router.get("", response_model=list[SessionListItem])
def list_sessions(c: C, active: Annotated[bool, Query()] = False) -> list[SessionListItem]:
    """List recent sessions (``active=true`` → READY/IN_PROGRESS/PAUSED only; used for resume after restart)."""
    with c.uow() as db:
        statuses = [SessionStatus.READY, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED] if active else None
        items = []
        for s in SessionRepository(db).list(statuses):
            cur = presenters.current_step(s)
            items.append(
                SessionListItem(
                    session_id=s.id, recipe_title=s.recipe_title, status=s.status,
                    current_step_position=cur.position if cur else None, total_steps=len(s.steps),
                    updated_at=ensure_utc(s.updated_at),
                )
            )
        return items


@router.get("/{session_id}", response_model=CookingSessionOut, responses=ERRORS)
def get_session(session_id: str, c: C) -> CookingSessionOut:
    with c.uow() as db:
        return presenters.to_session_out(SessionService(db, c.clock).load(session_id))


def _step_action(session_id: str, c: C, key: str | None, endpoint: str, action: Callable[[SessionService, Any], str]) -> Any:
    with c.uow() as db:
        svc = SessionService(db, c.clock)

        def run() -> StepResponse:
            s = svc.load(session_id)
            speech = action(svc, s)
            db.flush()
            return StepResponse(session=presenters.to_session_out(s), speech=speech)

        return _idempotent(db, key, session_id, endpoint, run)


@router.post("/{session_id}/start", response_model=StepResponse, responses=ERRORS)
def start(session_id: str, c: C, key: IdemKey = None, body: VersionedRequest | None = None) -> Any:
    return _step_action(session_id, c, key, "start", lambda svc, s: svc.start(s, _expected(body)))


@router.post("/{session_id}/next", response_model=StepResponse, responses=ERRORS)
def next_step(session_id: str, c: C, key: IdemKey = None, body: VersionedRequest | None = None) -> Any:
    """Complete the current step and advance. Send ``expectedVersion`` (and/or ``Idempotency-Key``)
    so a duplicated request cannot advance twice."""

    def act(svc: SessionService, s: Any) -> str:
        speech, completed = svc.next_step(s, _expected(body))
        if completed:
            TimerService(svc.db, c.clock).cancel_all(s.id)
        return speech

    return _step_action(session_id, c, key, "next", act)


@router.post("/{session_id}/previous", response_model=StepResponse, responses=ERRORS)
def previous_step(session_id: str, c: C, key: IdemKey = None, body: VersionedRequest | None = None) -> Any:
    return _step_action(session_id, c, key, "previous", lambda svc, s: svc.previous_step(s, _expected(body)))


@router.post("/{session_id}/pause", response_model=StepResponse, responses=ERRORS)
def pause(session_id: str, c: C, body: VersionedRequest | None = None) -> Any:
    return _step_action(session_id, c, None, "pause", lambda svc, s: svc.pause(s, _expected(body)))


@router.post("/{session_id}/resume", response_model=StepResponse, responses=ERRORS)
def resume(session_id: str, c: C, body: VersionedRequest | None = None) -> Any:
    return _step_action(session_id, c, None, "resume", lambda svc, s: svc.resume(s, _expected(body)))


@router.post("/{session_id}/complete", response_model=SessionSummary, responses=ERRORS)
def complete(session_id: str, c: C, body: VersionedRequest | None = None) -> SessionSummary:
    """End the session (any active status → COMPLETED), cancel timers, and summarize changes."""
    with c.uow() as db:
        svc = SessionService(db, c.clock)
        s = svc.load(session_id)
        summary = svc.complete(s, _expected(body))
        TimerService(db, c.clock).cancel_all(s.id)
        return summary


@router.post("/{session_id}/cancel", response_model=SessionSummary, responses=ERRORS)
def cancel(session_id: str, c: C, body: VersionedRequest | None = None) -> SessionSummary:
    with c.uow() as db:
        svc = SessionService(db, c.clock)
        s = svc.load(session_id)
        summary = svc.cancel(s, _expected(body))
        TimerService(db, c.clock).cancel_all(s.id)
        return summary


@router.get("/{session_id}/summary", response_model=SessionSummary, responses=ERRORS)
def summary(session_id: str, c: C) -> SessionSummary:
    with c.uow() as db:
        svc = SessionService(db, c.clock)
        return svc.summary(svc.load(session_id))


# ----------------------------------------------------------------- commands
@router.post("/{session_id}/commands", response_model=CommandResponse, responses=ERRORS)
def command(session_id: str, body: CommandRequest, c: C, key: IdemKey = None) -> Any:
    """Natural-language command (voice transcript or typed text)."""
    with c.uow() as db:
        svc = CommandService(db, c.clock, c.agent, c.settings)
        return _idempotent(
            db, key, session_id, "commands",
            lambda: svc.handle(SessionService(db, c.clock).load(session_id), body.text, body.expected_version),
        )


# ------------------------------------------------------------------ changes
@router.post("/{session_id}/changes/preview", response_model=ChangePreviewResponse, status_code=201, responses=ERRORS)
def preview_change(session_id: str, body: ChangePreviewRequest, c: C) -> ChangePreviewResponse:
    """Create a PROPOSED change (or NOT_APPLICABLE with a reason). Nothing is modified until /apply."""
    with c.uow() as db:
        s = SessionService(db, c.clock).load(session_id)
        return to_preview_response(ChangeService(db, c.clock, c.agent).preview(s, body))


@router.get("/{session_id}/changes", response_model=list[ChangePreviewResponse], responses=ERRORS)
def list_changes(session_id: str, c: C) -> list[ChangePreviewResponse]:
    with c.uow() as db:
        s = SessionService(db, c.clock).load(session_id)
        return [to_preview_response(ch, False) for ch in s.changes]


@router.post("/{session_id}/changes/{change_id}/apply", response_model=ApplyChangeResponse, responses=ERRORS)
def apply_change(session_id: str, change_id: str, c: C, key: IdemKey = None, body: VersionedRequest | None = None) -> Any:
    with c.uow() as db:

        def run() -> ApplyChangeResponse:
            s = SessionService(db, c.clock).load(session_id)
            ch = ChangeService(db, c.clock, c.agent).apply(s, change_id, _expected(body))
            db.flush()
            return ApplyChangeResponse(
                change_id=ch.id, status=ch.status, summary=ch.summary, speech=applied_speech(ch, s),
                session=presenters.to_session_out(s),
            )

        return _idempotent(db, key, session_id, f"apply:{change_id}", run)


@router.post("/{session_id}/changes/{change_id}/reject", response_model=ChangePreviewResponse, responses=ERRORS)
def reject_change(session_id: str, change_id: str, c: C) -> ChangePreviewResponse:
    with c.uow() as db:
        s = SessionService(db, c.clock).load(session_id)
        return to_preview_response(ChangeService(db, c.clock, c.agent).reject(s, change_id), False)


# ------------------------------------------------------------------- timers
@router.post("/{session_id}/timers", response_model=TimerOut, status_code=201, responses=ERRORS)
def create_timer(session_id: str, body: TimerCreateRequest, c: C) -> TimerOut:
    with c.uow() as db:
        s = SessionService(db, c.clock).load(session_id)
        svc = TimerService(db, c.clock)
        return svc.to_out(svc.start(s, body.duration_seconds, body.label))


@router.get("/{session_id}/timers", response_model=list[TimerOut], responses=ERRORS)
def list_timers(session_id: str, c: C) -> list[TimerOut]:
    with c.uow() as db:
        SessionService(db, c.clock).load(session_id)
        svc = TimerService(db, c.clock)
        return [svc.to_out(t) for t in svc.list(session_id)]


@router.post("/{session_id}/timers/{timer_id}/extend", response_model=TimerOut, responses=ERRORS)
def extend_timer(session_id: str, timer_id: str, body: TimerExtendRequest, c: C) -> TimerOut:
    with c.uow() as db:
        svc = TimerService(db, c.clock)
        return svc.to_out(svc.extend(session_id, timer_id, body.seconds))


@router.post("/{session_id}/timers/{timer_id}/cancel", response_model=TimerOut, responses=ERRORS)
def cancel_timer(session_id: str, timer_id: str, c: C) -> TimerOut:
    with c.uow() as db:
        svc = TimerService(db, c.clock)
        return svc.to_out(svc.cancel(session_id, timer_id))


@router.get("/{session_id}/events", response_model=EventsResponse, responses=ERRORS)
def poll_events(session_id: str, c: C, after_id: Annotated[int, Query(alias="afterId", ge=0)] = 0) -> EventsResponse:
    """Polling endpoint for the voice client: detects expired timers and returns new events."""
    with c.uow() as db:
        SessionService(db, c.clock).load(session_id)
        timers = TimerService(db, c.clock)
        all_timers = timers.list(session_id)
        events = SessionRepository(db).events_after(session_id, after_id)
        return EventsResponse(
            events=[
                SessionEventOut(event_id=e.id, type=e.type, message=e.message, payload=e.payload, created_at=ensure_utc(e.created_at))
                for e in events
            ],
            last_event_id=events[-1].id if events else after_id,
            timers=[timers.to_out(t) for t in all_timers if t.status == "RUNNING"],
        )
