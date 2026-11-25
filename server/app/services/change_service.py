"""Preview → (confirm) → apply workflow for session changes.

The agent (rule or LLM) can only contribute a validated ``SubstitutionProposal``; the
server builds the plan, persists it as PROPOSED, and applies it after re-validation.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from server.app.agents.base import AgentError, CookingAgent
from server.app.clock import Clock
from server.app.models import (
    CookingSessionChangeORM,
    CookingSessionIngredientORM,
    CookingSessionORM,
)
from server.app.parsers.ingredient_text import canonicalize
from server.app.schemas.agent import SubstitutionProposal
from server.app.schemas.change import (
    ChangePlan,
    ChangePreviewRequest,
    ChangePreviewResponse,
    ChangeStatus,
    ChangeType,
)
from server.app.schemas.session import StepStatus
from server.app.services import presenters
from server.app.services.change_planner import ChangePlanner, find_rule
from server.app.services.errors import InvalidState, NotFound
from server.app.services.presenters import josa
from server.app.services.session_service import ACTIVE_STATUSES, SessionService

log = logging.getLogger(__name__)


def _summary(req: ChangePreviewRequest, plan: ChangePlan, planner: ChangePlanner) -> str:
    used = f" ({', '.join(map(str, plan.already_used_in_steps))}단계는 이미 진행되어 이후 단계만 반영)" if plan.already_used_in_steps and req.type != ChangeType.SCALE else ""
    if req.type == ChangeType.EXCLUDE:
        ing = planner.resolve(req.ingredient or "")
        return f"{ing.name if ing else req.ingredient} 제외{used}"
    if req.type == ChangeType.SUBSTITUTE:
        ing = planner.resolve(req.ingredient or "")
        add = next((e for e in plan.ingredient_edits if e.action == "ADD"), None)
        amount = f" ({add.after})" if add and add.after and add.after != req.substitute else ""
        return f"{ing.name if ing else req.ingredient} → {req.substitute} 대체{amount}{used}"
    return f"{planner.s.servings:g}인분 → {req.servings:g}인분으로 재료량 조정"


class ChangeService:
    def __init__(self, db: Session, clock: Clock, agent: CookingAgent) -> None:
        self.db = db
        self.clock = clock
        self.agent = agent
        self.sessions = SessionService(db, clock)

    # ---------------------------------------------------------------- plan
    def _plan(self, s: CookingSessionORM, req: ChangePreviewRequest, stored_llm: dict | None) -> tuple[ChangePlan, str, dict | None]:
        planner = ChangePlanner(s)
        if req.type == ChangeType.EXCLUDE:
            return planner.plan_exclude(req.ingredient or ""), "RULE", None
        if req.type == ChangeType.SCALE:
            return planner.plan_scale(req.servings or 0), "RULE", None

        ing = planner.resolve(req.ingredient or "")
        rule = find_rule(ing, req.substitute or "")
        source, llm_payload = "RULE", None
        proposal: SubstitutionProposal | None = None
        if rule is not None:
            proposal = SubstitutionProposal(
                supported=True, substitute=req.substitute or rule.target, quantity_text=rule.amount,
                cooking_note=rule.cooking_note, reason="사전에 정의된 안전 대체 규칙",
            )
        elif stored_llm is not None:
            proposal, source, llm_payload = SubstitutionProposal.model_validate(stored_llm), "LLM", stored_llm
        elif ing is not None and self.agent.uses_llm:
            try:
                proposal = self.agent.propose_ingredient_substitution(
                    presenters.agent_context(s), ing.name, req.substitute or ""
                )
            except AgentError as exc:
                log.warning("substitution proposal failed: %s", exc)
                return ChangePlan(applicable=False, reason="AI 응답을 받지 못해 레시피를 바꾸지 않았어요. 다시 말씀해 주세요."), "LLM", None
            if proposal is not None:
                source, llm_payload = "LLM", proposal.model_dump()
        return planner.plan_substitute(req.ingredient or "", req.substitute or "", proposal, rule), source, llm_payload

    # ------------------------------------------------------------- preview
    def preview(self, s: CookingSessionORM, req: ChangePreviewRequest) -> CookingSessionChangeORM:
        self.sessions.require_status(s, *ACTIVE_STATUSES, action="레시피 변경")
        plan, source, llm_payload = self._plan(s, req, None)
        for old in s.changes:
            if old.status == ChangeStatus.PROPOSED:
                old.status = ChangeStatus.SUPERSEDED
        change = CookingSessionChangeORM(
            id=str(uuid.uuid4()),
            session_id=s.id,
            type=req.type,
            status=ChangeStatus.PROPOSED if plan.applicable else ChangeStatus.NOT_APPLICABLE,
            source=source,
            request={**req.model_dump(mode="json"), "llmProposal": llm_payload},
            plan=plan.model_dump(mode="json"),
            summary=_summary(req, plan, ChangePlanner(s)) if plan.applicable else (plan.reason or "적용 불가"),
            base_version=s.version,
            created_at=self.clock.now(),
        )
        s.changes.append(change)
        self.db.flush()
        return change

    # --------------------------------------------------------------- apply
    def get(self, s: CookingSessionORM, change_id: str) -> CookingSessionChangeORM:
        change = next((c for c in s.changes if c.id == change_id), None)
        if change is None:
            raise NotFound(f"변경안을 찾을 수 없습니다: {change_id}")
        return change

    def apply(self, s: CookingSessionORM, change_id: str, expected: int | None = None) -> CookingSessionChangeORM:
        self.sessions.require_status(s, *ACTIVE_STATUSES, action="레시피 변경 적용")
        self.sessions.check_version(s, expected)
        change = self.get(s, change_id)
        if change.status == ChangeStatus.APPLIED:
            return change  # idempotent
        if change.status != ChangeStatus.PROPOSED:
            raise InvalidState(f"이 변경안은 적용할 수 없는 상태입니다 ({change.status}).", changeStatus=change.status)

        plan = ChangePlan.model_validate(change.plan)
        if s.version != change.base_version:
            # Session moved on since the preview (e.g. a step was completed): re-plan on current state.
            req = ChangePreviewRequest.model_validate({k: v for k, v in change.request.items() if k != "llmProposal"})
            plan, _src, _ = self._plan(s, req, change.request.get("llmProposal"))
            change.plan = plan.model_dump(mode="json")
            if not plan.applicable:
                change.status = ChangeStatus.NOT_APPLICABLE
                change.summary = plan.reason or change.summary
                s.pending_action = None
                self.db.flush()
                raise InvalidState(plan.reason or "더 이상 적용할 수 없는 변경안입니다.", code="CHANGE_NOT_APPLICABLE")
        self._execute(s, plan)
        now = self.clock.now()
        self.sessions.touch(s)
        change.status = ChangeStatus.APPLIED
        change.applied_at = now
        change.applied_version = s.version
        s.pending_action = None
        self.sessions.repo.add_event(s.id, "CHANGE_APPLIED", change.summary, {"changeId": change.id}, now)
        self.db.flush()
        return change

    def reject(self, s: CookingSessionORM, change_id: str) -> CookingSessionChangeORM:
        change = self.get(s, change_id)
        if change.status == ChangeStatus.PROPOSED:
            change.status = ChangeStatus.REJECTED
        s.pending_action = None
        return change

    def _execute(self, s: CookingSessionORM, plan: ChangePlan) -> None:
        by_id = {i.id: i for i in s.ingredients}
        steps = {st.id: st for st in s.steps}
        for edit in plan.step_edits:  # validate first: never touch completed steps
            st = steps.get(edit.step_id)
            if st is None or st.status == StepStatus.COMPLETED:
                raise InvalidState(f"{edit.position}단계는 이미 완료되어 수정할 수 없습니다.", code="STEP_ALREADY_COMPLETED")
        for e in plan.ingredient_edits:
            target = by_id.get(e.ingredient_id or "")
            if e.action == "EXCLUDE" and target:
                target.status = "EXCLUDED"
            elif e.action == "REPLACE" and target:
                target.status = "REPLACED"
            elif e.action == "ANNOTATE" and target:
                target.note = f"{target.note} · {e.note}" if target.note else e.note
            elif e.action == "RESCALE" and target:
                target.quantity, target.quantity_text = e.quantity, e.quantity_text
            elif e.action == "ADD":
                src = by_id.get(e.substitute_for_id or "")
                s.ingredients.append(
                    CookingSessionIngredientORM(
                        id=str(uuid.uuid4()), position=max(i.position for i in s.ingredients) + 1, name=e.name,
                        canonical=canonicalize(e.name), raw_text=e.after or e.name, quantity=e.quantity,
                        quantity_text=e.quantity_text, unit=e.unit,
                        note=(e.note or "") + (f" ({src.name} 대체)" if src else ""),
                        status="ADDED", substitute_for_id=e.substitute_for_id, origin="CHANGE",
                    )
                )
        for edit in plan.step_edits:
            st = steps[edit.step_id]
            st.text = edit.after
            st.notes = [*(st.notes or []), *edit.added_notes]
            if edit.ingredient_refs is not None:
                st.ingredient_refs = edit.ingredient_refs
        if plan.new_servings is not None:
            s.servings = plan.new_servings


def to_preview_response(change: CookingSessionChangeORM, requires_confirmation: bool = True) -> ChangePreviewResponse:
    return ChangePreviewResponse(
        change_id=change.id,
        type=change.type,
        status=change.status,
        source=change.source,
        summary=change.summary,
        requires_confirmation=requires_confirmation and change.status == ChangeStatus.PROPOSED,
        plan=ChangePlan.model_validate(change.plan),
        base_version=change.base_version,
    )


def applied_speech(change: CookingSessionChangeORM, s: CookingSessionORM) -> str:
    plan = ChangePlan.model_validate(change.plan)
    req = change.request
    if change.type == ChangeType.EXCLUDE:
        name = next((e.name for e in plan.ingredient_edits), req.get("ingredient", ""))
        tail = " 이후 단계에도 반영할게요." if plan.step_edits else ""
        return f"{name}{josa(name, ('을', '를'))} 제외하고 진행할게요.{tail}"
    if change.type == ChangeType.SUBSTITUTE:
        src = next((e.name for e in plan.ingredient_edits if e.action != "ADD"), req.get("ingredient", ""))
        sub = req.get("substitute", "")
        return f"{src} 대신 {sub}{josa(sub, ('을', '를'))} 쓰도록 바꿨어요. 남은 단계는 바뀐 레시피로 안내할게요."
    return f"{s.servings:g}인분 기준으로 재료량을 바꿨어요."
