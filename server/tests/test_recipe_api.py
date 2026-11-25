"""Recipe import API: URL (fixture transport), manual fallback, validation, confirmation."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.tests.support import RECIPE_URL


def test_import_and_get(client: TestClient) -> None:
    r = client.post("/api/recipes/import", json={"url": RECIPE_URL})
    assert r.status_code == 201
    body = r.json()
    assert body["confirmed"] is False
    assert body["recipe"]["sourceUrl"] == RECIPE_URL and body["recipe"]["sourceType"] == "url"
    got = client.get(f"/api/recipes/{body['recipeId']}").json()
    assert got["recipe"] == body["recipe"]
    assert client.get("/api/recipes", params={"q": "제육"}).json()[0]["recipeId"] == body["recipeId"]


def test_session_requires_confirmed_recipe(client: TestClient) -> None:
    rid = client.post("/api/recipes/import", json={"url": RECIPE_URL}).json()["recipeId"]
    r = client.post("/api/cooking-sessions", json={"recipeId": rid})
    assert r.status_code == 409
    client.post(f"/api/recipes/{rid}/confirm")
    assert client.post("/api/cooking-sessions", json={"recipeId": rid}).status_code == 201


def test_import_validation(client: TestClient) -> None:
    assert client.post("/api/recipes/import", json={}).status_code == 422
    assert client.post("/api/recipes/import", json={"url": RECIPE_URL, "text": "x"}).status_code == 422
    assert client.get("/api/recipes/nope").status_code == 404


def test_fetch_failure_returns_clear_error_with_fallback(make_container: Callable) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(503))
    with TestClient(create_app(make_container(transport=transport))) as c:
        r = c.post("/api/recipes/import", json={"url": RECIPE_URL})
    assert r.status_code == 502
    assert r.json()["error"] == "RECIPE_FETCH_FAILED"
    assert "text" in r.json()["details"]["fallback"]


def test_manual_text_import(client: TestClient) -> None:
    text = "[재료]\n돼지고기 600g\n양파 1개\n[조리]\n1. 고기를 볶는다\n2. 양파를 넣고 볶는다"
    r = client.post("/api/recipes/import", json={"text": text, "title": "직접 입력 제육", "servings": 2})
    assert r.status_code == 201, r.text
    assert r.json()["recipe"]["sourceType"] == "manual"
    assert r.json()["parser"] == "manual:text"
