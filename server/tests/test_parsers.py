"""#1 레시피 URL 파싱 / #2 구조 검증 (fixture 기반, 네트워크 없음)."""

from __future__ import annotations

import httpx
import pytest

from server.app.parsers.builder import build_recipe
from server.app.parsers.errors import (
    RecipeFetchError,
    RecipeFetchForbidden,
    RecipeParseError,
)
from server.app.parsers.fetcher import HtmlFetcher
from server.app.parsers.ingredient_text import (
    IngredientLexeme,
    aliases_for,
    find_ingredient_refs,
)
from server.app.parsers.quantity import (
    parse_ingredient_line,
    parse_iso_duration_minutes,
)
from server.app.parsers.tenk_recipe import parse_tenk_html
from server.app.parsers.text_recipe import parse_recipe_text
from server.app.services.recipe_service import parse_html
from server.tests.support import RECIPE_URL, fixture_html, fixture_transport


def test_jsonld_parse_of_jeyuk_fixture() -> None:
    recipe, parser, warnings = parse_html(RECIPE_URL, fixture_html())
    assert parser == "10000recipe:jsonld"
    assert "제육볶음" in recipe.title
    assert recipe.servings == 2
    assert recipe.total_time_minutes == 30  # from JSON-LD totalTime PT30M
    assert len(recipe.ingredients) == 12
    assert len(recipe.steps) == 9
    pork = recipe.ingredients[0]
    assert (pork.name, pork.canonical, pork.quantity, pork.unit) == ("돼지고기앞다리살", "돼지고기", 1.0, "근")
    carrot = next(i for i in recipe.ingredients if i.name == "당근")
    assert (carrot.quantity_text, carrot.unit) == ("1/3", "개")
    assert any("정확한 수량이 없는 재료" in w for w in warnings)


def test_no_invented_quantities_or_times() -> None:
    recipe, _, _ = parse_html(RECIPE_URL, fixture_html())
    chili = next(i for i in recipe.ingredients if i.name == "고추")
    assert chili.quantity is None and chili.note == "기호에맞게"
    # Only step 8 states minutes ("불끄기3분전"); others must stay None.
    assert [s.duration_minutes for s in recipe.steps] == [None] * 7 + [3, None]


def test_html_fallback_when_jsonld_missing() -> None:
    raw, parser = parse_tenk_html(fixture_html("10000recipe_6845428_no_jsonld.html"))
    recipe = build_recipe(raw, source_type="fixture", source_url=RECIPE_URL)
    assert parser == "10000recipe:html"
    assert len(recipe.ingredients) == 12 and len(recipe.steps) == 9
    assert recipe.total_time_minutes is None  # "30분 이내" is an upper bound → not stored as exact time
    assert recipe.ingredients[6].group == "재육볶음양념장"


def test_step_ingredient_refs_respect_compound_words() -> None:
    recipe, _, _ = parse_html(RECIPE_URL, fixture_html())
    refs = {s.position: s.ingredient_refs for s in recipe.steps}
    assert "고추" not in refs[2]  # "고추가루", "고추장" are different ingredients
    assert {"고춧가루", "고추장"} <= set(refs[2])
    assert refs[3] == ["대파", "양파", "당근"]  # "파와 양파": 파 must not match inside 양파
    assert refs[8] == ["고추", "대파"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("돼지고기앞다리살 1근", ("돼지고기앞다리살", 1.0, "1", "근", None)),
        ("당근 1/3개", ("당근", 1 / 3, "1/3", "개", None)),
        ("고추 기호에맞게", ("고추", None, None, None, "기호에맞게")),
        ("후추가루 약간", ("후추가루", None, None, None, "약간")),
        ("돼지고기600g", ("돼지고기", 600.0, "600", "g", None)),
        ("간장 1과1/2스푼", ("간장", 1.5, "1과1/2", "스푼", None)),
        ("설탕 반스푼", ("설탕", 0.5, "반", "스푼", None)),
    ],
)
def test_ingredient_line_parsing(line: str, expected: tuple) -> None:
    p = parse_ingredient_line(line)
    assert (p.name, p.quantity, p.quantity_text, p.unit, p.note) == expected


def test_iso_duration() -> None:
    assert parse_iso_duration_minutes("PT30M") == 30
    assert parse_iso_duration_minutes("PT1H15M") == 75
    assert parse_iso_duration_minutes("garbage") is None


def test_alias_matching_guards() -> None:
    lex = [IngredientLexeme("고추", aliases_for("고추", ["고추"])), IngredientLexeme("양파", aliases_for("양파"))]
    assert find_ingredient_refs("고추장 1스푼, 고추가루 2스푼", lex) == []
    assert find_ingredient_refs("나머지 고추와 파등을", lex) == ["고추"]


def test_manual_text_recipe() -> None:
    raw = parse_recipe_text("제목: 간단 제육\n인분: 2\n[재료]\n돼지고기 600g, 양파 1개\n고추장 2스푼\n[조리]\n1. 고기를 볶는다\n2. 양파를 넣는다")
    recipe = build_recipe(raw, source_type="manual", source_url=None)
    assert recipe.title == "간단 제육" and len(recipe.ingredients) == 3 and len(recipe.steps) == 2
    assert recipe.steps[1].ingredient_refs == ["양파"]


def test_manual_text_requires_sections() -> None:
    with pytest.raises(RecipeParseError):
        parse_recipe_text("제목: x\n그냥 아무 글")


def test_empty_page_is_clear_parse_error() -> None:
    with pytest.raises(RecipeParseError):
        parse_html(RECIPE_URL, "<html><body>no recipe</body></html>")


def test_robots_disallow_is_respected() -> None:
    fetcher = HtmlFetcher(user_agent="test", timeout=5, transport=fixture_transport(robots="User-Agent: *\nDisallow: /recipe/\n"))
    with pytest.raises(RecipeFetchForbidden):
        fetcher.fetch(RECIPE_URL)


def test_fetch_http_error_and_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403 if "blocked" in request.url.path else 500)

    fetcher = HtmlFetcher(user_agent="t", timeout=5, transport=httpx.MockTransport(handler))
    with pytest.raises(RecipeFetchForbidden):
        fetcher.fetch("https://www.10000recipe.com/recipe/blocked")
    with pytest.raises(RecipeFetchError):
        fetcher.fetch("https://www.10000recipe.com/recipe/1")
