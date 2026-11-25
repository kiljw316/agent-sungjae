"""Builds a ``ChangePlan`` (pure computation, no DB writes) for a session snapshot.

Invariants
  * COMPLETED steps are never edited.
  * An ingredient already used in a completed step is never recorded as simply "replaced":
    it stays ACTIVE with a note, and the change only affects remaining steps.
  * Rule mode allows only curated substitutions; others are rejected with a clear reason.
"""

from __future__ import annotations

import re

from server.app.agents.knowledge import (
    EXCLUSION_NOTES,
    SubstitutionRule,
    find_substitution,
    substitutes_for,
)
from server.app.models import (
    CookingSessionIngredientORM,
    CookingSessionORM,
    CookingSessionStepORM,
)
from server.app.parsers.ingredient_text import aliases_for, canonicalize, catalog_entry
from server.app.parsers.quantity import format_quantity
from server.app.schemas.agent import SubstitutionProposal
from server.app.schemas.change import ChangePlan, IngredientEdit, StepEdit
from server.app.schemas.session import IngredientStatus, StepStatus
from server.app.services.presenters import active_ingredients, ingredient_display, josa
from server.app.services.step_text import remove_ingredient, replace_ingredient


def _ul(word: str) -> str:
    return word + josa(word, ("을", "를"))


def _eun(word: str) -> str:
    return word + josa(word, ("은", "는"))


class ChangePlanner:
    def __init__(self, session: CookingSessionORM) -> None:
        self.s = session

    # --------------------------------------------------------- resolution
    def resolve(self, phrase: str) -> CookingSessionIngredientORM | None:
        compact = phrase.replace(" ", "")
        canonical = canonicalize(compact)
        candidates = active_ingredients(self.s)
        for ing in candidates:
            if ing.name.replace(" ", "") == compact:
                return ing
        for ing in candidates:
            if ing.canonical == canonical or ing.canonical.split("#")[0] == canonical:
                return ing
        return None

    def resolve_any(self, phrase: str) -> CookingSessionIngredientORM | None:
        canonical = canonicalize(phrase.replace(" ", ""))
        return next((i for i in self.s.ingredients if i.canonical == canonical or i.name == phrase), None)

    def _aliases(self, ing: CookingSessionIngredientORM) -> list[str]:
        return aliases_for(ing.canonical.split("#")[0], [ing.name])

    def _ref_steps(self, ing: CookingSessionIngredientORM) -> tuple[list[CookingSessionStepORM], list[CookingSessionStepORM]]:
        refs = [st for st in self.s.steps if ing.canonical in (st.ingredient_refs or [])]
        done = [st for st in refs if st.status == StepStatus.COMPLETED]
        todo = [st for st in refs if st.status != StepStatus.COMPLETED]
        return done, todo

    def _in_progress_warning(self, todo: list[CookingSessionStepORM], name: str) -> list[str]:
        cur = [st for st in todo if st.status == StepStatus.IN_PROGRESS]
        if cur:
            return [f"지금 진행 중인 {cur[0].position}단계에도 {name}{josa(name, ('이', '가'))} 들어가요. 이미 넣었다면 알려 주세요."]
        return []

    # ------------------------------------------------------------ exclude
    def plan_exclude(self, phrase: str) -> ChangePlan:
        ing = self.resolve(phrase)
        if ing is None:
            gone = self.resolve_any(phrase)
            if gone is not None and gone.status in (IngredientStatus.EXCLUDED, IngredientStatus.REPLACED):
                return ChangePlan(applicable=False, reason=f"{_eun(gone.name)} 이미 제외했어요.")
            return ChangePlan(applicable=False, reason=f"이 레시피에는 {phrase}{josa(phrase, ('이', '가'))} 없어요.")
        entry = catalog_entry(ing.canonical)
        if (entry and entry.role == "main") or ing.position == 1 and entry is None:
            return ChangePlan(
                applicable=False,
                reason=f"{_eun(ing.name)} 이 요리의 주재료라서 빼면 레시피가 성립하지 않아요. 대체 재료가 있다면 말씀해 주세요.",
            )
        done, todo = self._ref_steps(ing)
        if done and not todo:
            used = ", ".join(str(st.position) for st in done)
            return ChangePlan(
                applicable=False,
                already_used_in_steps=[st.position for st in done],
                reason=f"{_eun(ing.name)} 이미 {used}단계에서 사용했어요. 이미 넣은 재료는 뺄 수 없어서 기록만 그대로 둘게요.",
            )
        warnings: list[str] = []
        if ing.canonical in EXCLUSION_NOTES:
            warnings.append(EXCLUSION_NOTES[ing.canonical])
        elif entry and entry.role == "seasoning":
            warnings.append("양념 맛이 달라질 수 있어요.")
        warnings += self._in_progress_warning(todo, ing.name)

        aliases = self._aliases(ing)
        step_edits = []
        for st in todo:
            new_text, clean = remove_ingredient(st.text, aliases)
            note = f"※ {_eun(ing.name)} 제외했어요." if clean else f"※ {_eun(ing.name)} 제외하고 진행하세요."
            step_edits.append(
                StepEdit(
                    step_id=st.id, position=st.position, before=st.text, after=new_text, added_notes=[note],
                    ingredient_refs=[r for r in st.ingredient_refs if r != ing.canonical],
                )
            )
        if done:
            used = ", ".join(str(st.position) for st in done)
            edit = IngredientEdit(
                action="ANNOTATE", ingredient_id=ing.id, name=ing.name, before=ingredient_display(ing),
                note=f"{used}단계에서 사용함 · 이후 단계 제외",
            )
        else:
            edit = IngredientEdit(
                action="EXCLUDE", ingredient_id=ing.id, name=ing.name, before=ingredient_display(ing), after="제외"
            )
        return ChangePlan(
            applicable=True,
            ingredient_edits=[edit],
            step_edits=step_edits,
            already_used_in_steps=[st.position for st in done],
            warnings=warnings,
        )

    # --------------------------------------------------------- substitute
    def plan_substitute(
        self, phrase: str, substitute: str, proposal: SubstitutionProposal | None, rule: SubstitutionRule | None
    ) -> ChangePlan:
        ing = self.resolve(phrase)
        if ing is None:
            gone = self.resolve_any(phrase)
            if gone is not None and gone.status in (IngredientStatus.EXCLUDED, IngredientStatus.REPLACED):
                what = "제외했어요" if gone.status == IngredientStatus.EXCLUDED else "다른 재료로 바꿨어요"
                return ChangePlan(applicable=False, reason=f"{_eun(gone.name)} 이미 {what}.")
            return ChangePlan(applicable=False, reason=f"이 레시피에는 {phrase}{josa(phrase, ('이', '가'))} 없어요.")
        target = substitute.strip()
        if rule is None and (canonicalize(target) == ing.canonical or target == ing.name):
            return ChangePlan(applicable=False, reason=f"{target}{josa(target, ('은', '는'))} 원래 재료와 같은 재료예요.")
        if proposal is None or not proposal.supported:
            options = substitutes_for(ing.canonical)
            hint = f" 규칙으로 지원하는 대체 재료: {', '.join(options)}." if options else ""
            why = f" ({proposal.reason})" if proposal and proposal.reason else ""
            return ChangePlan(
                applicable=False,
                reason=f"{ing.name} 대신 {_ul(target)} 쓰는 건 안전하게 판단할 수 있는 규칙이 없어요.{why}{hint}",
            )
        done, todo = self._ref_steps(ing)
        if done and not todo:
            used = ", ".join(str(st.position) for st in done)
            return ChangePlan(
                applicable=False,
                already_used_in_steps=[st.position for st in done],
                reason=f"{_eun(ing.name)} 이미 {used}단계에서 모두 사용했어요. 남은 단계에는 {ing.name}{josa(ing.name, ('이', '가'))} 쓰이지 않아요.",
            )
        warnings = self._in_progress_warning(todo, ing.name)
        target_canonical = canonicalize(target)
        existing = next((i for i in active_ingredients(self.s) if i.canonical == target_canonical), None)
        if existing:
            warnings.append(f"레시피에 이미 {existing.name}{josa(existing.name, ('이', '가'))} 있어요. {ing.name} 몫만큼 추가로 준비하세요.")

        aliases = self._aliases(ing)
        overrides = rule.phrase_overrides if rule else {}
        step_edits = []
        for idx, st in enumerate(todo):
            new_text = replace_ingredient(st.text, aliases, target, overrides)
            if target_canonical in (st.ingredient_refs or []):
                # Step already mentions the substitute (e.g. "파와 양파" → avoid "파와 대파"): drop the source instead.
                removed, clean = remove_ingredient(st.text, aliases)
                if clean:
                    new_text = removed
            notes = [f"※ {ing.name} 대신 {_ul(target)} 사용해요."]
            if idx == 0 and proposal.cooking_note:
                notes.append(proposal.cooking_note)
            refs = [target_canonical if r == ing.canonical else r for r in st.ingredient_refs]
            step_edits.append(
                StepEdit(
                    step_id=st.id, position=st.position, before=st.text, after=new_text, added_notes=notes,
                    ingredient_refs=list(dict.fromkeys(refs)),
                )
            )

        quantity, qtext, unit = None, None, None
        if rule and rule.quantity_ratio is not None and ing.quantity is not None:
            quantity = round(ing.quantity * rule.quantity_ratio, 3)
            qtext, unit = format_quantity(quantity), rule.target_unit or ing.unit
        add = IngredientEdit(
            action="ADD", name=target, after=f"{target} {qtext + (unit or '') if qtext else proposal.quantity_text or ''}".strip(),
            quantity=quantity, quantity_text=qtext, unit=unit,
            note=None if qtext else (proposal.quantity_text or None), substitute_for_id=ing.id,
        )
        if done:
            used = ", ".join(str(st.position) for st in done)
            src = IngredientEdit(
                action="ANNOTATE", ingredient_id=ing.id, name=ing.name, before=ingredient_display(ing),
                note=f"{used}단계에서 사용함 · 이후 {target}로 대체",
            )
        else:
            src = IngredientEdit(
                action="REPLACE", ingredient_id=ing.id, name=ing.name, before=ingredient_display(ing), after=f"{target}(으)로 대체"
            )
        return ChangePlan(
            applicable=True,
            ingredient_edits=[src, add],
            step_edits=step_edits,
            already_used_in_steps=[st.position for st in done],
            warnings=warnings,
        )

    # -------------------------------------------------------------- scale
    def plan_scale(self, servings: float) -> ChangePlan:
        base = self.s.servings
        if base is None:
            return ChangePlan(applicable=False, reason="원본 레시피에 인분 정보가 없어서 인분을 바꿀 수 없어요.")
        if abs(servings - base) < 1e-9:
            return ChangePlan(applicable=False, reason=f"이미 {format_quantity(base)}인분 기준이에요.")
        factor = servings / base
        edits: list[IngredientEdit] = []
        vague: list[str] = []
        for ing in active_ingredients(self.s):
            if ing.quantity is None:
                vague.append(ing.name)
                continue
            new_q = round(ing.quantity * factor, 3)
            edits.append(
                IngredientEdit(
                    action="RESCALE", ingredient_id=ing.id, name=ing.name, before=ingredient_display(ing),
                    after=f"{ing.name} {format_quantity(new_q)}{ing.unit or ''}", quantity=new_q,
                    quantity_text=format_quantity(new_q),
                )
            )
        f_text = format_quantity(factor)
        note = f"※ 재료량은 {format_quantity(servings)}인분 기준(×{f_text})이에요. 단계에 적힌 양도 같은 비율로 맞춰 주세요."
        step_edits = [
            StepEdit(step_id=st.id, position=st.position, before=st.text, after=st.text, added_notes=[note])
            for st in self.s.steps
            if st.status != StepStatus.COMPLETED and re.search(r"\d|반\s*(스푼|수저|컵|개)", st.text)
        ]
        warnings = []
        if vague:
            warnings.append(f"{', '.join(vague)}{josa(vague[-1], ('은', '는'))} 원본에 정확한 양이 없어 입맛에 맞게 조절해 주세요.")
        done = [st.position for st in self.s.steps if st.status == StepStatus.COMPLETED]
        if done:
            warnings.append(f"이미 진행한 {', '.join(map(str, done))}단계의 재료는 원래 양으로 들어갔어요.")
        return ChangePlan(
            applicable=True, ingredient_edits=edits, step_edits=step_edits, warnings=warnings,
            new_servings=servings, scale_factor=factor, already_used_in_steps=done,
        )


def find_rule(ingredient: CookingSessionIngredientORM | None, substitute: str) -> SubstitutionRule | None:
    if ingredient is None:
        return None
    return find_substitution(ingredient.canonical, canonicalize(substitute)) or find_substitution(
        ingredient.canonical, substitute
    )
