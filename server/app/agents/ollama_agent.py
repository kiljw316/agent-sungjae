"""Local LLM via Ollama's /api/chat with JSON-schema constrained output."""

from __future__ import annotations

import httpx

from server.app.agents.base import AgentError
from server.app.agents.llm_base import LLMCookingAgent


class OllamaCookingAgent(LLMCookingAgent):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self.model = model

    def _complete_json(self, system: str, user: str, schema: dict[str, object]) -> str:
        try:
            resp = self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": schema,
                    "options": {"temperature": 0},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
            )
            resp.raise_for_status()
            return str(resp.json()["message"]["content"])
        except httpx.TimeoutException as exc:
            raise AgentError("ollama timeout") from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise AgentError(f"ollama error: {exc}") from exc

    def is_available(self) -> bool:
        try:
            tags = self._client.get("/api/tags", timeout=2.0).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return False
        return any(m.get("name", "").split(":")[0] == self.model.split(":")[0] for m in tags)
