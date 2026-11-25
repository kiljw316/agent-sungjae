"""ORM → DTO mapping and spoken-sentence formatting (single place for user-facing wording)."""

from __future__ import annotations

from server.app.clock import ensure_utc
from server.app.models import (
    CookingSessionChangeORM,
    CookingSessionIngredientORM,
    CookingSessionORM,
    CookingSessionStepORM,
)
from server.app.parsers.quantity import format_quantity
from server.app.schemas.agent import AgentContext
from server.app.schemas.session import (
    AppliedChangeOut,
    CookingSessionOut,
    IngredientStatus,
    SessionIngredientOut,
    SessionStepOut,
)


def ingredient_amount(ing: CookingSessionIngredientORM) -> str:
    qty = ing.quantity_text or (format_quantity(ing.quantity) if ing.quantity is not None else None)
    parts = [f"{qty}{ing.unit or ''}" if qty else "", ing.note or ""]
    return " ".join(p for p in parts if p)


def ingredient_display(ing: CookingSessionIngredientORM) -> str:
    amount = ingredient_amount(ing)
    text = f"{ing.name} {amount}".strip()
    if ing.status == IngredientStatus.EXCLUDED:
        return f"{text} (제외)"
    if ing.status == IngredientStatus.REPLACED:
        return f"{text} (대체됨)"
    return text


def current_step(s: CookingSessionORM) -> CookingSessionStepORM | None:
    return next((st for st in s.steps if st.id == s.current_step_id), None)


def step_by_position(s: CookingSessionORM, position: int) -> CookingSessionStepORM | None:
    return next((st for st in s.steps if st.position == position), None)


def to_change_out(c: CookingSessionChangeORM) -> AppliedChangeOut:
    return AppliedChangeOut(
        change_id=c.id,
        type=c.type,
        status=c.status,
        source=c.source,
        summary=c.summary,
        created_at=ensure_utc(c.created_at),
        applied_at=ensure_utc(c.applied_at) if c.applied_at else None,
    )


def to_session_out(s: CookingSessionORM) -> CookingSessionOut:
    cur = current_step(s)
    return CookingSessionOut(
        session_id=s.id,
        recipe_id=s.recipe_id,
        recipe_title=s.recipe_title,
        status=s.status,
        current_step_id=s.current_step_id,
        current_step_position=cur.position if cur else None,
        total_steps=len(s.steps),
        version=s.version,
        servings=s.servings,
        base_servings=s.base_servings,
        ingredients=[
            SessionIngredientOut(
                id=i.id, name=i.name, canonical=i.canonical, raw_text=i.raw_text, quantity=i.quantity,
                quantity_text=i.quantity_text, unit=i.unit, note=i.note, status=i.status,
                substitute_for_id=i.substitute_for_id, origin=i.origin, display=ingredient_display(i),
            )
            for i in s.ingredients
        ],
        steps=[
            SessionStepOut(
                id=st.id, position=st.position, text=st.text, original_text=st.original_text, notes=list(st.notes or []),
                ingredient_refs=list(st.ingredient_refs or []), duration_minutes=st.duration_minutes, status=st.status,
                completed_at=ensure_utc(st.completed_at) if st.completed_at else None,
            )
            for st in s.steps
        ],
        applied_changes=[to_change_out(c) for c in s.changes if c.status == "APPLIED"],
        pending_action=s.pending_action,
        created_at=ensure_utc(s.created_at),
        updated_at=ensure_utc(s.updated_at),
    )


def step_speech(s: CookingSessionORM, step: CookingSessionStepORM, prefix: str = "") -> str:
    total = len(s.steps)
    head = "마지막 단계예요." if step.position == total else f"{step.position}단계예요."
    if step.position == 1:
        head = f"총 {total}단계 중 1단계예요."
    notes = " ".join(step.notes or [])
    return " ".join(p for p in (prefix, head, step.text, notes) if p).strip()


def active_ingredients(s: CookingSessionORM) -> list[CookingSessionIngredientORM]:
    return [i for i in s.ingredients if i.status in (IngredientStatus.ACTIVE, IngredientStatus.ADDED)]


def agent_context(s: CookingSessionORM) -> AgentContext:
    cur = current_step(s)
    return AgentContext(
        recipe_title=s.recipe_title,
        status=s.status,
        current_step_position=cur.position if cur else None,
        total_steps=len(s.steps),
        current_step_text=cur.text if cur else None,
        current_step_duration_minutes=cur.duration_minutes if cur else None,
        active_ingredients=[i.name for i in active_ingredients(s)],
        all_ingredients=[i.name for i in s.ingredients],
        steps=[f"{st.position}. [{st.status}] {st.text}" for st in s.steps],
    )


def josa(word: str, pair: tuple[str, str]) -> str:
    """Pick Korean particle by final consonant: josa("고추", ("은", "는")) -> "는"."""
    last = word[-1] if word else ""
    has_batchim = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return pair[0] if has_batchim else pair[1]
