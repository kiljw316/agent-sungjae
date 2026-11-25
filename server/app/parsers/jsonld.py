"""schema.org/Recipe JSON-LD extraction (preferred path)."""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from server.app.parsers.builder import RawRecipe
from server.app.parsers.quantity import parse_iso_duration_minutes, parse_servings


def _iter_nodes(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [n for item in data for n in _iter_nodes(item)]
    if isinstance(data, dict):
        if "@graph" in data:
            return _iter_nodes(data["@graph"])
        return [data]
    return []


def _is_recipe(node: dict[str, Any]) -> bool:
    t = node.get("@type")
    return t == "Recipe" or (isinstance(t, list) and "Recipe" in t)


def _instructions(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line for line in value.splitlines() if line.strip()]
    out: list[str] = []
    for item in value or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            if item.get("@type") == "HowToSection":
                out.extend(_instructions(item.get("itemListElement")))
            elif item.get("text"):
                out.append(str(item["text"]))
    return out


def extract_jsonld_recipe(soup: BeautifulSoup) -> RawRecipe | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        try:
            data = json.loads(raw, strict=False)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_nodes(data):
            if not _is_recipe(node):
                continue
            return RawRecipe(
                title=str(node.get("name") or ""),
                ingredient_lines=[(None, str(x)) for x in node.get("recipeIngredient") or []],
                step_texts=_instructions(node.get("recipeInstructions")),
                servings=parse_servings(node.get("recipeYield")),
                total_time_minutes=parse_iso_duration_minutes(node.get("totalTime"))
                or parse_iso_duration_minutes(node.get("cookTime")),
                description=node.get("description"),
            )
    return None
