from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app.agents.hybrid import HybridCookingAgent
from server.app.agents.rule_based import RuleBasedCookingAgent
from server.app.api.deps import Container
from server.app.clock import FakeClock
from server.app.config import Settings
from server.app.main import build_container, create_app
from server.app.parsers.fetcher import HtmlFetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECIPE_URL = "https://www.10000recipe.com/recipe/6845428"


def fixture_html(name: str = "10000recipe_6845428.html") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_transport(page: str = "10000recipe_6845428.html", robots: str = "User-Agent: *\nDisallow: /admin/\n") -> httpx.MockTransport:
    """Serves the captured fixture instead of the live site (no network in default tests)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path == "/recipe/6845428":
            return httpx.Response(200, text=fixture_html(page))
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def make_container(db_url: str, clock: FakeClock) -> Callable[..., Container]:
    def _make(transport: httpx.BaseTransport | None = None, agent: HybridCookingAgent | None = None, **overrides: object) -> Container:
        settings = Settings(database_url=db_url, agent_provider="rule", **overrides)  # type: ignore[arg-type]
        fetcher = HtmlFetcher(user_agent="test", timeout=5, transport=transport or fixture_transport())
        c = build_container(settings, clock=clock, fetcher=fetcher)
        if agent is not None:
            c.agent = agent
        return c

    return _make


@pytest.fixture
def client(make_container: Callable[..., Container]) -> Iterator[TestClient]:
    with TestClient(create_app(make_container())) as tc:
        yield tc


def import_confirmed(client: TestClient) -> str:
    r = client.post("/api/recipes/import", json={"url": RECIPE_URL})
    assert r.status_code == 201, r.text
    recipe_id = r.json()["recipeId"]
    assert client.post(f"/api/recipes/{recipe_id}/confirm").status_code == 200
    return recipe_id


def new_session(client: TestClient, start: bool = True) -> dict:
    recipe_id = import_confirmed(client)
    r = client.post("/api/cooking-sessions", json={"recipeId": recipe_id})
    assert r.status_code == 201, r.text
    s = r.json()
    if start:
        r = client.post(f"/api/cooking-sessions/{s['sessionId']}/start")
        assert r.status_code == 200, r.text
        s = r.json()["session"]
    return s


def say(client: TestClient, sid: str, text: str, **extra: object) -> dict:
    r = client.post(f"/api/cooking-sessions/{sid}/commands", json={"text": text, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def rule_agent() -> HybridCookingAgent:
    return HybridCookingAgent(RuleBasedCookingAgent(), None)
