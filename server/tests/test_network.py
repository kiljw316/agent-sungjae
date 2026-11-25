"""Real-network tests (opt-in: ``pytest --run-network``). Distinct from fixture-based tests."""

from __future__ import annotations

import pytest

from server.app.config import Settings
from server.app.parsers.fetcher import HtmlFetcher
from server.app.services.recipe_service import parse_html
from server.tests.support import RECIPE_URL

pytestmark = pytest.mark.network


def test_live_fetch_and_parse_jeyuk() -> None:
    s = Settings()
    html = HtmlFetcher(user_agent=s.user_agent, timeout=20).fetch(RECIPE_URL)
    recipe, parser, _ = parse_html(RECIPE_URL, html)
    assert parser == "10000recipe:jsonld"
    assert "제육볶음" in recipe.title
    assert len(recipe.steps) >= 5 and len(recipe.ingredients) >= 5
    assert any(i.canonical == "돼지고기" for i in recipe.ingredients)
