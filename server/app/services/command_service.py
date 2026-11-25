"""Natural-language command → intent → service call. All business rules live in services;
this module only routes, manages pending confirmations and phrases replies."""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from server.app.agents.base import AgentError
from server.app.agents.hybrid import HybridCookingAgent
from server.app.agents.knowledge import FOOD_SAFETY_DONENESS, substitutes_for
from server.app.clock import Clock
from server.app.config import Settings
from server.app.models import CookingSessionORM
from server.app.parsers.ingredient_text import catalog_entry
from server.app.schemas.agent import IntentResult
from server.app.schemas.change import (
    ChangePlan,
    ChangePreviewRequest,
    ChangeStatus,
    ChangeType,
)
from server.app.schemas.command import CommandResponse, Intent
from server.app.schemas.session import SessionStatus
from server.app.services import presenters
from server.app.services.change_planner import ChangePlanner
from server.app.services.change_service import (
    ChangeService,
    applied_speech,
    to_preview_response,
)
from server.app.services.errors import InvalidState
from server.app.services.presenters import josa
from server.app.services.session_service import SessionService
from server.app.services.timer_service import TimerService, format_seconds

log = logging.getLogger(__name__)

MUTATING = {
    Intent.START_COOKING, Intent.PAUSE, Intent.RESUME, Intent.NEXT_STEP, Intent.GO_PREVIOUS,
    Intent.EXCLUDE_INGREDIENT, Intent.SUBSTITUTE_INGREDIENT, Intent.SCALE_SERVINGS, Intent.CONFIRM,
}
READ_ONLY = {
    Intent.REPEAT_STEP, Intent.CURRENT_STEP_NUMBER, Intent.REMAINING_STEPS, Intent.LIST_INGREDIENTS,
    Intent.TIMER_STATUS, Intent.COOKING_QUESTION, Intent.PREVIOUS_STEP_INFO,
}
DONENESS_RE = re.compile(r"익었|익은지|덜\s*익|속까지|핏기|안\s*익")


class CommandService:
    def __init__(self, db: Session, clock: Clock, agent: HybridCookingAgent, settings: Settings) -> None:
        self.db = db
        self.clock = clock
        self.agent = agent
        self.settings = settings
        self.sessions = SessionService(db, clock)
        self.changes = ChangeService(db, clock, agent)
        self.timers = TimerService(db, clock)

    def _resp(self, s: CookingSessionORM, intent: Intent, speech: str, **kw: object) -> CommandResponse:
        self.db.flush()
        return CommandResponse(
            intent=intent, speech=speech, handled_by=self.agent.last_source, session=presenters.to_session_out(s), **kw
        )

    # ------------------------------------------------------------------ entry
    def handle(self, s: CookingSessionORM, text: str, expected: int | None = None) -> CommandResponse:
        ctx = presenters.agent_context(s)
        result = self.agent.classify_intent(text, ctx)
        intent = result.intent
        log.info("command %r → %s (%s)", text, intent, self.agent.last_source)

        if s.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED) and intent not in READ_ONLY:
            return self._resp(s, intent, "이미 끝난 요리 세션이에요. 새 세션을 시작해 주세요.")
        if intent in MUTATING:
            self.sessions.check_version(s, expected)
        if intent not in READ_ONLY | {Intent.CONFIRM, Intent.DENY, Intent.KEEP_INGREDIENT}:
            self._drop_pending(s)

        handler = getattr(self, f"_on_{intent.value.lower()}", None)
        if handler is None:
            return self._resp(s, intent, "잘 못 알아들었어요. '다음 단계', '다시 설명해 줘', '고추 빼줘'처럼 말씀해 주세요.")
        try:
            return handler(s, result, text)
        except InvalidState as exc:
            return self._resp(s, intent, exc.message)

    def _drop_pending(self, s: CookingSessionORM) -> None:
        pending = s.pending_action or {}
        if pending.get("kind") == "APPLY_CHANGE":
            for c in s.changes:
                if c.id == pending.get("changeId") and c.status == ChangeStatus.PROPOSED:
                    c.status = ChangeStatus.SUPERSEDED
        s.pending_action = None

    def _guard_paused(self, s: CookingSessionORM) -> None:
        if s.status == SessionStatus.PAUSED:
            raise InvalidState("지금은 일시정지 중이에요. '다시 시작'이라고 말해 주세요.")

    # --------------------------------------------------------------- session
    def _on_start_cooking(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        before = s.version
        speech = self.sessions.start(s)
        return self._resp(s, r.intent, speech, state_changed=s.version != before)

    def _on_pause(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        before = s.version
        speech = self.sessions.pause(s)
        return self._resp(s, r.intent, speech, state_changed=s.version != before)

    def _on_resume(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        if s.status == SessionStatus.READY:
            return self._on_start_cooking(s, r, text)
        before = s.version
        speech = self.sessions.resume(s)
        return self._resp(s, r.intent, speech, state_changed=s.version != before)

    def _on_end_session(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        s.pending_action = {"kind": "COMPLETE_SESSION"}
        return self._resp(s, r.intent, "요리를 종료할까요? '응' 또는 '아니'로 답해 주세요.", requires_confirmation=True)

    # ----------------------------------------------------------------- steps
    def _on_next_step(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        self._guard_paused(s)
        speech, completed = self.sessions.next_step(s)
        summary = None
        if completed:
            self.timers.cancel_all(s.id)
            summary = self.sessions.summary(s)
        return self._resp(s, r.intent, speech, state_changed=True, summary=summary)

    def _on_go_previous(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        self._guard_paused(s)
        speech = self.sessions.previous_step(s)
        return self._resp(s, r.intent, speech, state_changed=True)

    def _on_repeat_step(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        cur = presenters.current_step(s)
        if cur is None:
            return self._resp(s, r.intent, "진행 중인 단계가 없어요.")
        prefix = "아직 시작 전이에요. 첫 단계는 이래요." if s.status == SessionStatus.READY else "다시 말씀드릴게요."
        return self._resp(s, r.intent, presenters.step_speech(s, cur, prefix))

    def _on_previous_step_info(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        cur = presenters.current_step(s)
        prev = presenters.step_by_position(s, cur.position - 1) if cur else None
        if prev is None:
            return self._resp(s, r.intent, "지금이 첫 단계라 이전 단계가 없어요.")
        notes = " ".join(prev.notes or [])
        return self._resp(s, r.intent, f"이전 {prev.position}단계는 이랬어요. {prev.text} {notes}".strip())

    def _on_current_step_number(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        return self._resp(s, r.intent, self.sessions.position_speech(s))

    def _on_remaining_steps(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        return self._resp(s, r.intent, self.sessions.remaining_speech(s))

    # ----------------------------------------------------------- ingredients
    def _on_list_ingredients(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        active = presenters.active_ingredients(s)
        excluded = [i.name for i in s.ingredients if i.status in ("EXCLUDED", "REPLACED")]
        speech = "지금 사용할 재료는 " + ", ".join(presenters.ingredient_display(i) for i in active) + "예요."
        if excluded:
            speech += f" 빠진 재료는 {', '.join(excluded)}예요."
        return self._resp(s, r.intent, speech)

    def _on_keep_ingredient(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        name = r.ingredient or "그 재료"
        ing = ChangePlanner(s).resolve_any(name) if r.ingredient else None
        if ing is not None and ing.status in ("EXCLUDED", "REPLACED"):
            return self._resp(
                s, r.intent,
                f"{ing.name}{josa(ing.name, ('은', '는'))} 이미 레시피에서 뺐어요. 변경 되돌리기는 아직 지원하지 않아요.",
            )
        pending = s.pending_action or {}
        if pending.get("kind") == "APPLY_CHANGE":
            change = next((c for c in s.changes if c.id == pending.get("changeId")), None)
            if change and change.type == ChangeType.EXCLUDE:
                self.changes.reject(s, change.id)
        return self._resp(s, r.intent, f"알겠어요. {name}{josa(name, ('은', '는'))} 빼지 않고 그대로 넣을게요.")

    def _propose(
        self, s: CookingSessionORM, r: IntentResult, req: ChangePreviewRequest, auto_apply: bool, ask: str,
        question: str = "이렇게 바꿀까요?",
    ) -> CommandResponse:
        change = self.changes.preview(s, req)
        plan = ChangePlan.model_validate(change.plan)
        if not plan.applicable:
            return self._resp(s, r.intent, plan.reason or "이 변경은 적용할 수 없어요.", change=to_preview_response(change, False))
        warn = " ".join(plan.warnings)
        if auto_apply and not plan.warnings:
            change = self.changes.apply(s, change.id)
            return self._resp(
                s, r.intent, applied_speech(change, s), state_changed=True, change=to_preview_response(change, False)
            )
        s.pending_action = {"kind": "APPLY_CHANGE", "changeId": change.id}
        return self._resp(
            s, r.intent, f"{ask} {warn} {question}".replace("  ", " ").strip(),
            requires_confirmation=True, change=to_preview_response(change),
        )

    def _on_exclude_ingredient(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        name = r.ingredient or ""
        ing = ChangePlanner(s).resolve(name)
        display = ing.name if ing else name
        entry = catalog_entry(ing.canonical) if ing else None
        low_impact = entry is not None and entry.role in ("garnish", "secondary")
        auto = r.explicit and low_impact and self.settings.auto_apply_explicit_exclusion
        ask = f"네, {display}{josa(display, ('은', '는'))} 빼도 괜찮아요."
        if not low_impact:
            ask = f"{display}{josa(display, ('을', '를'))} 빼면 맛이 달라질 수 있어요."
        if ing and not r.explicit and (opts := substitutes_for(ing.canonical)):
            ask += f" {', '.join(opts)}{josa(opts[-1], ('으로', '로'))} 대체할 수도 있어요."
        question = f"레시피에서 {display}{josa(display, ('을', '를'))} 제외할까요?"
        return self._propose(s, r, ChangePreviewRequest(type=ChangeType.EXCLUDE, ingredient=name), auto, ask, question)

    def _on_substitute_ingredient(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        if not r.substitute:
            ing = ChangePlanner(s).resolve(r.ingredient or "")
            opts = substitutes_for(ing.canonical) if ing else []
            if not opts:
                return self._resp(s, r.intent, f"{r.ingredient or '그 재료'}의 안전한 대체 규칙이 아직 없어요. 빼고 진행할 수는 있어요.")
            return self._resp(s, r.intent, f"{ing.name if ing else r.ingredient} 대신 {', '.join(opts)}를 쓸 수 있어요. 무엇으로 바꿀까요?")
        req = ChangePreviewRequest(type=ChangeType.SUBSTITUTE, ingredient=r.ingredient, substitute=r.substitute)
        change = self.changes.preview(s, req)
        plan = ChangePlan.model_validate(change.plan)
        add = next((e for e in plan.ingredient_edits if e.action == "ADD"), None)
        ask = f"{r.ingredient} 대신 {r.substitute}{josa(r.substitute, ('을', '를'))} 쓸 수 있어요."
        amount = add.after.removeprefix(r.substitute).strip() if add and add.after else ""
        if amount:
            ask += f" 양은 {amount}{josa(amount, ('으로', '로'))} 하세요."
        if plan.step_edits:
            first_note = next((n for n in plan.step_edits[0].added_notes[1:]), None)
            if first_note:
                ask += f" {first_note}"
        if plan.already_used_in_steps:
            ask += f" {r.ingredient}{josa(r.ingredient or '', ('은', '는'))} 이미 {', '.join(map(str, plan.already_used_in_steps))}단계에서 사용해서 남은 단계만 바꿀게요."
        if change.source == "LLM":
            ask += " (AI 제안이라 확인이 필요해요.)"
        if not plan.applicable:
            return self._resp(s, r.intent, plan.reason or "바꿀 수 없어요.", change=to_preview_response(change, False))
        warn = " ".join(w for w in plan.warnings if "진행 중인" in w or "이미" in w)
        s.pending_action = {"kind": "APPLY_CHANGE", "changeId": change.id}
        return self._resp(
            s, r.intent, f"{ask} {warn}".strip() + " 이렇게 바꿀까요?", requires_confirmation=True,
            change=to_preview_response(change),
        )

    def _on_scale_servings(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        req = ChangePreviewRequest(type=ChangeType.SCALE, servings=r.servings)
        ask = f"{r.servings:g}인분으로 재료량을 바꿀게요."
        if s.servings:
            ask += f" 지금보다 {r.servings / s.servings:g}배예요."
        return self._propose(s, r, req, auto_apply=False, ask=ask)

    # ----------------------------------------------------------- confirmation
    def _on_confirm(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        pending = s.pending_action or {}
        kind = pending.get("kind")
        if kind == "APPLY_CHANGE":
            change = self.changes.apply(s, str(pending["changeId"]))
            return self._resp(s, r.intent, applied_speech(change, s), state_changed=True, change=to_preview_response(change, False))
        if kind == "COMPLETE_SESSION":
            summary = self.sessions.complete(s)
            self.timers.cancel_all(s.id)
            return self._resp(s, r.intent, summary.speech, state_changed=True, summary=summary)
        if s.status == SessionStatus.READY:
            return self._on_start_cooking(s, r, text)
        return self._resp(s, r.intent, "네. 다음으로 넘어가려면 '다음 단계'라고 말해 주세요.")

    def _on_deny(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        pending = s.pending_action or {}
        if pending.get("kind") == "APPLY_CHANGE":
            self.changes.reject(s, str(pending["changeId"]))
            return self._resp(s, r.intent, "알겠어요. 레시피는 그대로 둘게요.")
        if pending.get("kind") == "COMPLETE_SESSION":
            s.pending_action = None
            return self._resp(s, r.intent, "계속 요리할게요.")
        return self._resp(s, r.intent, "알겠어요.")

    # ------------------------------------------------------------ questions
    def _on_cooking_question(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        try:
            answer = self.agent.answer_cooking_question(text, presenters.agent_context(s))
        except AgentError as exc:
            log.warning("LLM answer failed: %s", exc)
            return self._resp(s, r.intent, "AI 응답을 받지 못했어요. 요리 상태는 바뀌지 않았어요. 다시 질문해 주세요.")
        if answer is None:
            hint = "" if self.agent.uses_llm else " 지금은 규칙 기반 모드라 정해진 질문만 답할 수 있어요."
            return self._resp(s, r.intent, f"그 질문에는 확실하게 답하기 어려워요.{hint}")
        speech = answer.answer
        if answer.safety_note:
            speech += " " + answer.safety_note
        if DONENESS_RE.search(text) and FOOD_SAFETY_DONENESS not in speech:
            speech += " " + FOOD_SAFETY_DONENESS  # server-side safety guard regardless of provider
        return self._resp(s, r.intent, speech)

    def _on_unknown(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        return self._resp(s, r.intent, "잘 못 알아들었어요. '다음 단계', '다시 설명해 줘', '3분 타이머 시작'처럼 말씀해 주세요.")

    # ---------------------------------------------------------------- timers
    def _on_timer_start(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        t = self.timers.start(s, r.seconds or 60)
        return self._resp(s, r.intent, f"{format_seconds(t.duration_seconds)} 타이머를 시작했어요.", timer=self.timers.to_out(t))

    def _on_timer_add(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        running = self.timers.running(s.id)
        secs = r.seconds or 60
        if not running:
            t = self.timers.start(s, secs)
            return self._resp(s, r.intent, f"진행 중인 타이머가 없어서 {format_seconds(secs)} 타이머를 새로 시작했어요.", timer=self.timers.to_out(t))
        t = self.timers.extend(s.id, running[-1].id, secs)
        return self._resp(
            s, r.intent, f"{format_seconds(secs)} 추가했어요. 남은 시간은 {format_seconds(self.timers.remaining(t))}이에요.",
            timer=self.timers.to_out(t),
        )

    def _on_timer_status(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        running = self.timers.running(s.id)
        if not running:
            return self._resp(s, r.intent, "진행 중인 타이머가 없어요.")
        parts = [f"{t.label} 타이머 {format_seconds(self.timers.remaining(t))}" for t in running]
        return self._resp(s, r.intent, "남은 시간은 " + ", ".join(parts) + " 남았어요.", timer=self.timers.to_out(running[-1]))

    def _on_timer_cancel(self, s: CookingSessionORM, r: IntentResult, text: str) -> CommandResponse:
        running = self.timers.running(s.id)
        if not running:
            return self._resp(s, r.intent, "취소할 타이머가 없어요.")
        t = self.timers.cancel(s.id, running[-1].id)
        return self._resp(s, r.intent, f"{t.label} 타이머를 취소했어요.", timer=self.timers.to_out(t))

