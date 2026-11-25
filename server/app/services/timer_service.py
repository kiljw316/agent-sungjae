"""Session timers. The persisted ``ends_at`` is the single source of truth, so remaining time
survives server restarts and is never counted by an LLM. Expiry is detected lazily on every
read (polling) and emitted once as a TIMER_FINISHED event."""

from __future__ import annotations

import math
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from server.app.clock import Clock, ensure_utc
from server.app.models import CookingSessionORM, TimerORM
from server.app.repositories.session_repo import SessionRepository
from server.app.schemas.timer import TimerOut, TimerStatus
from server.app.services import presenters
from server.app.services.errors import InvalidState, NotFound


def format_seconds(seconds: int) -> str:
    m, s = divmod(max(seconds, 0), 60)
    if m and s:
        return f"{m}분 {s}초"
    return f"{m}분" if m else f"{s}초"


class TimerService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self.repo = SessionRepository(db)

    def remaining(self, t: TimerORM) -> int:
        if t.status != TimerStatus.RUNNING:
            return 0
        return max(0, math.ceil((ensure_utc(t.ends_at) - self.clock.now()).total_seconds()))

    def to_out(self, t: TimerORM) -> TimerOut:
        return TimerOut(
            timer_id=t.id, label=t.label, status=t.status, duration_seconds=t.duration_seconds,
            ends_at=ensure_utc(t.ends_at), remaining_seconds=self.remaining(t), created_at=ensure_utc(t.created_at),
        )

    def sweep(self, session_id: str) -> list[TimerORM]:
        """Mark expired timers FINISHED and emit one event per timer. Idempotent."""
        now = self.clock.now()
        finished = []
        for t in self.repo.timers(session_id, [TimerStatus.RUNNING]):
            if ensure_utc(t.ends_at) <= now:
                t.status = TimerStatus.FINISHED
                t.finished_at = now
                self.repo.add_event(
                    session_id, "TIMER_FINISHED", f"{t.label} 타이머가 끝났어요.", {"timerId": t.id, "label": t.label}, now
                )
                finished.append(t)
        self.db.flush()
        return finished

    def list(self, session_id: str) -> list[TimerORM]:
        self.sweep(session_id)
        return self.repo.timers(session_id)

    def running(self, session_id: str) -> list[TimerORM]:
        self.sweep(session_id)
        return self.repo.timers(session_id, [TimerStatus.RUNNING])

    def start(self, s: CookingSessionORM, seconds: int, label: str | None = None) -> TimerORM:
        if s.status in ("COMPLETED", "CANCELLED"):
            raise InvalidState("종료된 세션에는 타이머를 만들 수 없어요.")
        now = self.clock.now()
        cur = presenters.current_step(s)
        t = TimerORM(
            id=str(uuid.uuid4()), session_id=s.id,
            label=label or (f"{cur.position}단계 {format_seconds(seconds)}" if cur else format_seconds(seconds)),
            duration_seconds=seconds, ends_at=now + timedelta(seconds=seconds), status=TimerStatus.RUNNING, created_at=now,
        )
        self.db.add(t)
        self.db.flush()
        return t

    def _get_running(self, session_id: str, timer_id: str) -> TimerORM:
        self.sweep(session_id)
        t = self.repo.get_timer(session_id, timer_id)
        if t is None:
            raise NotFound(f"타이머를 찾을 수 없습니다: {timer_id}")
        if t.status != TimerStatus.RUNNING:
            raise InvalidState(f"이미 {t.status} 상태인 타이머입니다.", timerStatus=t.status)
        return t

    def extend(self, session_id: str, timer_id: str, seconds: int) -> TimerORM:
        t = self._get_running(session_id, timer_id)
        t.ends_at = ensure_utc(t.ends_at) + timedelta(seconds=seconds)
        t.duration_seconds += seconds
        self.db.flush()
        return t

    def cancel(self, session_id: str, timer_id: str) -> TimerORM:
        t = self._get_running(session_id, timer_id)
        t.status = TimerStatus.CANCELLED
        t.finished_at = self.clock.now()
        self.db.flush()
        return t

    def cancel_all(self, session_id: str) -> int:
        running = self.running(session_id)
        for t in running:
            t.status = TimerStatus.CANCELLED
            t.finished_at = self.clock.now()
        return len(running)
