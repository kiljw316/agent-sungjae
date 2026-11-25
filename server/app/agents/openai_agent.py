"""OpenAI Chat Completions with structured JSON output. Requires an official API key (no session scraping)."""

from __future__ import annotations

import httpx

from server.app.agents.base import AgentError
from server.app.agents.llm_base import LLMCookingAgent


class OpenAICookingAgent(LLMCookingAgent):
    name = "openai"

    def __init__(
        self, api_key: str, model: str, base_url: str, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport, headers={"Authorization": f"Bearer {api_key}"}
        )
        self.model = model

    def _complete_json(self, system: str, user: str, schema: dict[str, object]) -> str:
        try:
            resp = self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "cooking_agent_output", "schema": schema, "strict": False},
                    },
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                raise AgentError("empty completion")
            return str(content)
        except httpx.TimeoutException as exc:
            raise AgentError("openai timeout") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise AgentError(f"openai error: {exc}") from exc
