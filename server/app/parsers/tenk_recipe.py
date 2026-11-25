"""만개의레시피 (10000recipe.com) parser: JSON-LD first, HTML markup as fallback/enrichment."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from server.app.parsers.builder import RawRecipe
from server.app.parsers.jsonld import extract_jsonld_recipe

DOMAIN = "10000recipe.com"


def is_tenk_url(url: str) -> bool:
    return bool(re.match(r"^https?://(www\.|m\.)?10000recipe\.com/recipe/\d+", url))


def _html_fallback(soup: BeautifulSoup) -> RawRecipe | None:
    title_el = soup.select_one(".view2_summary h3")
    area = soup.select_one("#divConfirmedMaterialArea")
    step_els = soup.select("div.view_step_cont .media-body")
    if not (title_el and area and step_els):
        return None
    lines: list[tuple[str | None, str]] = []
    group: str | None = None
    for el in area.select("ul > b, ul > li"):
        if el.name == "b":
            group = el.get_text(strip=True).strip("[]") or None
            continue
        name_el = el.select_one(".ingre_list_name")
        amount_el = el.select_one(".ingre_list_ea")
        name = name_el.get_text(" ", strip=True) if name_el else ""
        amount = amount_el.get_text(" ", strip=True) if amount_el else ""
        if name:
            lines.append((group, f"{name} {amount}".strip()))
    servings_el = soup.select_one(".view2_summary_info1")
    servings = None
    if servings_el and (m := re.search(r"(\d+)", servings_el.get_text())):
        servings = float(m.group(1))
    # ".view2_summary_info2" says e.g. "30분 이내" — an upper bound, so it is NOT stored as an exact time.
    return RawRecipe(
        title=title_el.get_text(strip=True),
        ingredient_lines=lines,
        step_texts=[el.get_text(" ", strip=True) for el in step_els],
        servings=servings,
    )


def parse_tenk_html(html: str) -> tuple[RawRecipe, str]:
    """Return (raw recipe, parser name). Raises nothing; caller validates emptiness."""
    soup = BeautifulSoup(html, "html.parser")
    from_ld = extract_jsonld_recipe(soup)
    from_html = _html_fallback(soup)
    if from_ld and from_ld.ingredient_lines and from_ld.step_texts:
        if from_html:
            # HTML keeps ingredient group labels (e.g. [양념]); JSON-LD wins for everything else.
            if len(from_html.ingredient_lines) == len(from_ld.ingredient_lines):
                from_ld.ingredient_lines = from_html.ingredient_lines
            from_ld.servings = from_ld.servings or from_html.servings
        return from_ld, "10000recipe:jsonld"
    if from_html:
        from_html.warnings.append("JSON-LD가 없어 HTML 구조에서 추출했습니다.")
        return from_html, "10000recipe:html"
    return RawRecipe(title="", ingredient_lines=[], step_texts=[]), "10000recipe:none"
