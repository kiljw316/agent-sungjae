"""Thin HTTP client for the FastAPI server. Contains no cooking logic."""

from __future__ import annotations

import uuid
from typing import Any

import httpx


class ServerUnavailable(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{status} {code}] {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class CookingApiClient:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    @classmethod
    def connect(cls, base_url: str, timeout: float = 30.0) -> CookingApiClient:
        return cls(httpx.Client(base_url=base_url, timeout=timeout))

    def _request(self, method: str, path: str, *, retries: int = 0, **kw: Any) -> Any:
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                resp = self.http.request(method, path, **kw)
                break
            except httpx.TransportError as exc:  # connection refused, timeouts, ...
                last = exc
        else:
            raise ServerUnavailable(f"서버에 연결할 수 없습니다 ({self.http.base_url}): {last}") from last
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            raise ApiError(resp.status_code, body.get("error", "HTTP_ERROR"), body.get("message", resp.text[:200]), body.get("details"))
        return resp.json()

    # health / recipes ---------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def import_url(self, url: str) -> dict[str, Any]:
        return self._request("POST", "/api/recipes/import", json={"url": url})

    def import_text(self, text: str, title: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/recipes/import", json={"text": text, "title": title})

    def find_recipes(self, query: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/api/recipes", params={"q": query} if query else None)

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/recipes/{recipe_id}")

    def confirm_recipe(self, recipe_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/recipes/{recipe_id}/confirm")

    # sessions ------------------------------------------------------------
    def create_session(self, recipe_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/cooking-sessions", json={"recipeId": recipe_id})

    def active_sessions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/cooking-sessions", params={"active": "true"})

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/cooking-sessions/{session_id}")

    def command(self, session_id: str, text: str, expected_version: int | None = None) -> dict[str, Any]:
        # Same Idempotency-Key on retry → a lost response can't advance the step twice.
        key = str(uuid.uuid4())
        body: dict[str, Any] = {"text": text}
        if expected_version is not None:
            body["expectedVersion"] = expected_version
        return self._request(
            "POST", f"/api/cooking-sessions/{session_id}/commands", json=body, headers={"Idempotency-Key": key}, retries=1
        )

    def events(self, session_id: str, after_id: int) -> dict[str, Any]:
        return self._request("GET", f"/api/cooking-sessions/{session_id}/events", params={"afterId": after_id})
