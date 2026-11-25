"""Manual fallback: user pastes recipe text when URL import fails.

Accepted format (headings are flexible)::

    제목: 제육볶음
    인분: 2
    [재료]
    돼지고기 600g
    양파 1개
    [조리]
    1. 고기를 양념에 재운다
    2. 센불에 볶는다
"""

from __future__ import annotations

import re

from server.app.parsers.builder import RawRecipe
from server.app.parsers.errors import RecipeParseError

_ING_HEAD = re.compile(r"^\[?\s*(재료|ingredients?)\s*\]?\s*:?$", re.I)
_STEP_HEAD = re.compile(r"^\[?\s*(조리\s*(순서|단계|방법)?|만드는\s*법|순서|steps?|instructions?)\s*\]?\s*:?$", re.I)
_STEP_PREFIX = re.compile(r"^\s*(\d+)\s*[.)]\s*|^\s*step\s*\d+\s*[:.]?\s*", re.I)


def parse_recipe_text(text: str, title: str | None = None, servings: float | None = None) -> RawRecipe:
    section: str | None = None
    found_title = title
    found_servings = servings
    ingredients: list[tuple[str | None, str]] = []
    steps: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if m := re.match(r"^(제목|title)\s*:\s*(.+)$", line, re.I):
            found_title = found_title or m.group(2).strip()
            continue
        if m := re.match(r"^(인분|servings?)\s*:\s*(\d+(?:\.\d+)?)", line, re.I):
            found_servings = found_servings or float(m.group(2))
            continue
        if _ING_HEAD.match(line):
            section = "ing"
            continue
        if _STEP_HEAD.match(line):
            section = "step"
            continue
        if section == "ing":
            for part in re.split(r"\s*[,，]\s*", line):
                if part:
                    ingredients.append((None, part))
        elif section == "step":
            steps.append(_STEP_PREFIX.sub("", line))
    if not found_title:
        raise RecipeParseError("제목이 필요합니다. '제목: ...' 줄을 넣거나 title 필드를 지정하세요.")
    if section is None:
        raise RecipeParseError("'[재료]'와 '[조리]' 구분 줄이 필요합니다.")
    return RawRecipe(title=found_title, ingredient_lines=ingredients, step_texts=steps, servings=found_servings)
