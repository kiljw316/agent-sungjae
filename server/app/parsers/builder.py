"""Turn raw extracted fields into a validated ``Recipe``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from server.app.parsers.errors import RecipeParseError
from server.app.parsers.ingredient_text import (
    IngredientLexeme,
    aliases_for,
    canonicalize,
    find_ingredient_refs,
)
from server.app.parsers.quantity import extract_step_minutes, parse_ingredient_line
from server.app.schemas.recipe import Ingredient, Recipe, RecipeStep, SourceType

MAX_DESCRIPTION = 120


@dataclass
class RawRecipe:
    title: str
    ingredient_lines: list[tuple[str | None, str]]  # (group, "name amount")
    step_texts: list[str]
    servings: float | None = None
    total_time_minutes: int | None = None
    description: str | None = None
    warnings: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_recipe(raw: RawRecipe, *, source_type: SourceType, source_url: str | None) -> Recipe:
    if not raw.title.strip():
        raise RecipeParseError("레시피 제목을 찾지 못했습니다.")
    if not raw.ingredient_lines:
        raise RecipeParseError("재료 목록을 찾지 못했습니다.")
    steps_text = [_clean(s) for s in raw.step_texts if _clean(s)]
    if not steps_text:
        raise RecipeParseError("조리 단계를 찾지 못했습니다.")

    ingredients: list[Ingredient] = []
    seen: set[str] = set()
    for group, line in raw.ingredient_lines:
        line = _clean(line)
        if not line:
            continue
        parsed = parse_ingredient_line(line)
        canonical = canonicalize(parsed.name)
        key = canonical
        suffix = 2
        while key in seen:  # same ingredient listed twice (e.g. 양념 그룹): keep both, distinct keys
            key = f"{canonical}#{suffix}"
            suffix += 1
        seen.add(key)
        ingredients.append(
            Ingredient(
                name=parsed.name,
                canonical=canonical,
                raw_text=line,
                quantity=parsed.quantity,
                quantity_text=parsed.quantity_text,
                unit=parsed.unit,
                note=parsed.note,
                group=group,
            )
        )

    lexemes = [IngredientLexeme(i.canonical, aliases_for(i.canonical, [i.name])) for i in ingredients]
    steps = [
        RecipeStep(
            position=idx,
            text=text,
            ingredient_refs=find_ingredient_refs(text, lexemes),
            duration_minutes=extract_step_minutes(text),
        )
        for idx, text in enumerate(steps_text, start=1)
    ]
    description = _clean(raw.description)[:MAX_DESCRIPTION] if raw.description else None
    try:
        return Recipe(
            title=_clean(raw.title),
            source_url=source_url,
            source_type=source_type,
            servings=raw.servings,
            total_time_minutes=raw.total_time_minutes,
            description=description or None,
            ingredients=ingredients,
            steps=steps,
        )
    except ValidationError as exc:
        raise RecipeParseError(f"레시피 구조 검증 실패: {exc.errors()[0]['msg']}") from exc
