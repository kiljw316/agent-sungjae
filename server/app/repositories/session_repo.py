from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from server.app.models import (
    CookingSessionChangeORM,
    CookingSessionORM,
    IdempotencyRecordORM,
    SessionEventORM,
    TimerORM,
)


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, session: CookingSessionORM) -> CookingSessionORM:
        self.db.add(session)
        return session

    def get(self, session_id: str) -> CookingSessionORM | None:
        stmt = (
            select(CookingSessionORM)
            .where(CookingSessionORM.id == session_id)
            .options(
                selectinload(CookingSessionORM.ingredients),
                selectinload(CookingSessionORM.steps),
                selectinload(CookingSessionORM.changes),
            )
        )
        return self.db.scalars(stmt).one_or_none()

    def list(self, statuses: list[str] | None = None, limit: int = 20) -> list[CookingSessionORM]:
        stmt = select(CookingSessionORM).order_by(CookingSessionORM.updated_at.desc()).limit(limit)
        if statuses:
            stmt = stmt.where(CookingSessionORM.status.in_(statuses))
        return list(self.db.scalars(stmt.options(selectinload(CookingSessionORM.steps))))

    def get_change(self, session_id: str, change_id: str) -> CookingSessionChangeORM | None:
        change = self.db.get(CookingSessionChangeORM, change_id)
        return change if change and change.session_id == session_id else None

    # timers / events -----------------------------------------------------
    def timers(self, session_id: str, statuses: list[str] | None = None) -> list[TimerORM]:
        stmt = select(TimerORM).where(TimerORM.session_id == session_id).order_by(TimerORM.created_at)
        if statuses:
            stmt = stmt.where(TimerORM.status.in_(statuses))
        return list(self.db.scalars(stmt))

    def get_timer(self, session_id: str, timer_id: str) -> TimerORM | None:
        timer = self.db.get(TimerORM, timer_id)
        return timer if timer and timer.session_id == session_id else None

    def add_event(self, session_id: str, type_: str, message: str, payload: dict[str, object], now: datetime) -> None:
        self.db.add(SessionEventORM(session_id=session_id, type=type_, message=message, payload=payload, created_at=now))

    def events_after(self, session_id: str, after_id: int) -> list[SessionEventORM]:
        stmt = (
            select(SessionEventORM)
            .where(SessionEventORM.session_id == session_id, SessionEventORM.id > after_id)
            .order_by(SessionEventORM.id)
        )
        return list(self.db.scalars(stmt))

    # idempotency ---------------------------------------------------------
    def get_idempotent(self, key: str) -> IdempotencyRecordORM | None:
        return self.db.get(IdempotencyRecordORM, key)

    def save_idempotent(self, record: IdempotencyRecordORM) -> None:
        self.db.add(record)
