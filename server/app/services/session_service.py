"""Cooking Session lifecycle and step navigation.

Concurrency: every state mutation calls ``_touch`` which bumps ``version``; the ORM issues
``UPDATE ... WHERE version = :loaded`` (optimistic lock). Callers may also pass
``expected_version`` so a duplicated "다음" request (same version) is rejected with 409
instead of advancing twice.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from server.app.clock import Clock, ensure_utc
from server.app.models import (
    CookingSessionIngredientORM,
    CookingSessionORM,
    CookingSessionStepORM,
)
from server.app.repositories.recipe_repo import RecipeRepository
from server.app.repositories.session_repo import SessionRepository
from server.app.schemas.recipe import Recipe
from server.app.schemas.session import SessionStatus, SessionSummary, StepStatus
from server.app.services import presenters
from server.app.services.errors import InvalidState, NotFound, VersionConflict

ACTIVE_STATUSES = (SessionStatus.READY, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED)


class SessionService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self.repo = SessionRepository(db)

    # ------------------------------------------------------------ helpers
    def load(self, session_id: str) -> CookingSessionORM:
        s = self.repo.get(session_id)
        if s is None:
            raise NotFound(f"세션을 찾을 수 없습니다: {session_id}", sessionId=session_id)
        return s

    @staticmethod
    def check_version(s: CookingSessionORM, expected: int | None) -> None:
        if expected is not None and expected != s.version:
            raise VersionConflict(
                "세션 상태가 이미 바뀌었습니다. 최신 상태를 다시 불러오세요.",
                expectedVersion=expected,
                currentVersion=s.version,
            )

    def touch(self, s: CookingSessionORM) -> None:
        s.version += 1
        s.updated_at = self.clock.now()

    @staticmethod
    def require_status(s: CookingSessionORM, *allowed: SessionStatus, action: str) -> None:
        if s.status not in allowed:
            raise InvalidState(f"현재 상태({s.status})에서는 '{action}'을(를) 할 수 없습니다.", status=s.status)

    # ------------------------------------------------------------ create
    def create(self, recipe_id: str) -> CookingSessionORM:
        recipe_orm = RecipeRepository(self.db).get(recipe_id)
        if recipe_orm is None:
            raise NotFound(f"레시피를 찾을 수 없습니다: {recipe_id}")
        if not recipe_orm.confirmed:
            raise InvalidState("레시피가 아직 확인되지 않았습니다. 먼저 POST /api/recipes/{id}/confirm 을 호출하세요.")
        recipe = Recipe.model_validate(recipe_orm.data)
        now = self.clock.now()
        s = CookingSessionORM(
            id=str(uuid.uuid4()),
            recipe_id=recipe_orm.id,
            recipe_title=recipe.title,
            status=SessionStatus.READY,
            version=1,
            servings=recipe.servings,
            base_servings=recipe.servings,
            created_at=now,
            updated_at=now,
        )
        # Session-scoped snapshot: the Recipe Definition itself is never modified.
        s.ingredients = [
            CookingSessionIngredientORM(
                id=str(uuid.uuid4()), position=idx, name=i.name, canonical=i.canonical, raw_text=i.raw_text,
                quantity=i.quantity, quantity_text=i.quantity_text, unit=i.unit, note=i.note, status="ACTIVE",
                origin="RECIPE",
            )
            for idx, i in enumerate(recipe.ingredients, start=1)
        ]
        s.steps = [
            CookingSessionStepORM(
                id=str(uuid.uuid4()), position=st.position, original_text=st.text, text=st.text, notes=[],
                ingredient_refs=st.ingredient_refs, duration_minutes=st.duration_minutes, status=StepStatus.PENDING,
            )
            for st in recipe.steps
        ]
        s.current_step_id = s.steps[0].id
        self.repo.add(s)
        self.db.flush()
        return s

    # ---------------------------------------------------------- lifecycle
    def start(self, s: CookingSessionORM, expected: int | None = None) -> str:
        if s.status == SessionStatus.IN_PROGRESS:
            cur = presenters.current_step(s)
            return presenters.step_speech(s, cur, "이미 요리 중이에요.") if cur else "이미 요리 중이에요."
        if s.status == SessionStatus.PAUSED:
            return self.resume(s, expected)
        self.require_status(s, SessionStatus.READY, action="요리 시작")
        self.check_version(s, expected)
        cur = presenters.current_step(s)
        assert cur is not None
        cur.status = StepStatus.IN_PROGRESS
        s.status = SessionStatus.IN_PROGRESS
        s.started_at = self.clock.now()
        self.touch(s)
        return presenters.step_speech(s, cur, f"{s.recipe_title} 요리를 시작할게요.")

    def next_step(self, s: CookingSessionORM, expected: int | None = None) -> tuple[str, bool]:
        """Complete the current step and move on. Returns (speech, session_completed)."""
        if s.status == SessionStatus.READY:
            return self.start(s, expected), False
        self.require_status(s, SessionStatus.IN_PROGRESS, action="다음 단계")
        self.check_version(s, expected)
        cur = presenters.current_step(s)
        assert cur is not None
        now = self.clock.now()
        cur.status = StepStatus.COMPLETED
        cur.completed_at = now
        nxt = presenters.step_by_position(s, cur.position + 1)
        s.pending_action = None
        if nxt is None:
            s.status = SessionStatus.COMPLETED
            s.ended_at = now
            self.touch(s)
            self.repo.add_event(s.id, "SESSION_COMPLETED", "모든 단계를 마쳤어요.", {}, now)
            return "모든 단계를 마쳤어요. " + self.summary(s).speech, True
        nxt.status = StepStatus.IN_PROGRESS
        s.current_step_id = nxt.id
        self.touch(s)
        return presenters.step_speech(s, nxt), False

    def previous_step(self, s: CookingSessionORM, expected: int | None = None) -> str:
        self.require_status(s, SessionStatus.IN_PROGRESS, action="이전 단계로 이동")
        self.check_version(s, expected)
        cur = presenters.current_step(s)
        assert cur is not None
        prev = presenters.step_by_position(s, cur.position - 1)
        if prev is None:
            raise InvalidState("지금이 첫 단계예요.", code="ALREADY_FIRST_STEP")
        cur.status = StepStatus.PENDING
        prev.status = StepStatus.IN_PROGRESS
        prev.completed_at = None
        s.current_step_id = prev.id
        s.pending_action = None
        self.touch(s)
        return presenters.step_speech(s, prev, "이전 단계로 돌아갈게요.")

    def pause(self, s: CookingSessionORM, expected: int | None = None) -> str:
        if s.status == SessionStatus.PAUSED:
            return "이미 일시정지 상태예요."
        self.require_status(s, SessionStatus.IN_PROGRESS, SessionStatus.READY, action="일시정지")
        self.check_version(s, expected)
        s.status = SessionStatus.PAUSED
        self.touch(s)
        return "잠깐 멈출게요. 준비되면 '다시 시작'이라고 말해 주세요. 실행 중인 타이머는 계속 흘러가요."

    def resume(self, s: CookingSessionORM, expected: int | None = None) -> str:
        if s.status == SessionStatus.IN_PROGRESS:
            return "이미 진행 중이에요."
        self.require_status(s, SessionStatus.PAUSED, action="다시 시작")
        self.check_version(s, expected)
        cur = presenters.current_step(s)
        assert cur is not None
        s.status = SessionStatus.IN_PROGRESS
        if cur.status == StepStatus.PENDING:
            cur.status = StepStatus.IN_PROGRESS
            s.started_at = s.started_at or self.clock.now()
        self.touch(s)
        return presenters.step_speech(s, cur, "다시 시작할게요.")

    def complete(self, s: CookingSessionORM, expected: int | None = None) -> SessionSummary:
        if s.status == SessionStatus.COMPLETED:
            return self.summary(s)
        self.require_status(s, *ACTIVE_STATUSES, action="요리 종료")
        self.check_version(s, expected)
        now = self.clock.now()
        s.status = SessionStatus.COMPLETED
        s.ended_at = now
        s.pending_action = None
        self.touch(s)
        self.repo.add_event(s.id, "SESSION_COMPLETED", "요리를 종료했어요.", {}, now)
        return self.summary(s)

    def cancel(self, s: CookingSessionORM, expected: int | None = None) -> SessionSummary:
        self.require_status(s, *ACTIVE_STATUSES, action="요리 취소")
        self.check_version(s, expected)
        s.status = SessionStatus.CANCELLED
        s.ended_at = self.clock.now()
        s.pending_action = None
        self.touch(s)
        return self.summary(s)

    # ------------------------------------------------------------- reads
    def summary(self, s: CookingSessionORM) -> SessionSummary:
        done = sum(1 for st in s.steps if st.status == StepStatus.COMPLETED)
        applied = [c for c in s.changes if c.status == "APPLIED"]
        if applied:
            changes = " ".join(f"{idx}. {c.summary}" for idx, c in enumerate(applied, start=1))
            change_text = f"이번 요리에서 바뀐 점은 {len(applied)}가지예요. {changes}"
        else:
            change_text = "레시피 변경 없이 원본대로 요리했어요."
        verb = "요리를 마쳤어요" if s.status == SessionStatus.COMPLETED else "요리를 중단했어요"
        speech = f"{s.recipe_title} {verb}. 전체 {len(s.steps)}단계 중 {done}단계를 진행했어요. {change_text}"
        return SessionSummary(
            session_id=s.id,
            recipe_title=s.recipe_title,
            status=s.status,
            completed_steps=done,
            total_steps=len(s.steps),
            applied_changes=[presenters.to_change_out(c) for c in applied],
            started_at=ensure_utc(s.started_at) if s.started_at else None,
            ended_at=ensure_utc(s.ended_at) if s.ended_at else None,
            speech=speech,
        )

    def remaining_speech(self, s: CookingSessionORM) -> str:
        cur = presenters.current_step(s)
        total = len(s.steps)
        if s.status == SessionStatus.COMPLETED:
            return "모든 단계를 마쳤어요."
        if cur is None:
            return f"총 {total}단계예요."
        left = total - cur.position
        if left == 0:
            return f"지금이 마지막 {total}단계예요."
        timed = [st.duration_minutes for st in s.steps if st.position > cur.position and st.duration_minutes]
        time_note = f" 원본에 시간이 적힌 단계는 합쳐서 약 {sum(timed)}분이에요." if timed else ""
        return f"지금 {cur.position}단계이고, 이 단계 다음으로 {left}단계가 남았어요.{time_note}"

    def position_speech(self, s: CookingSessionORM) -> str:
        cur = presenters.current_step(s)
        if cur is None:
            return "진행 중인 단계가 없어요."
        state = {"READY": "아직 시작 전이에요", "PAUSED": "일시정지 중이에요"}.get(s.status, "")
        return f"전체 {len(s.steps)}단계 중 {cur.position}단계예요. {state}".strip()
