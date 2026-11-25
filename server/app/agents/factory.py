from __future__ import annotations

import logging

from server.app.agents.base import CookingAgent
from server.app.agents.hybrid import HybridCookingAgent
from server.app.agents.ollama_agent import OllamaCookingAgent
from server.app.agents.openai_agent import OpenAICookingAgent
from server.app.agents.rule_based import RuleBasedCookingAgent
from server.app.config import Settings

log = logging.getLogger(__name__)


def build_agent(settings: Settings) -> HybridCookingAgent:
    llm: CookingAgent | None = None
    if settings.agent_provider == "ollama":
        llm = OllamaCookingAgent(settings.ollama_base_url, settings.ollama_model, settings.llm_timeout_seconds)
        log.info("Ollama agent enabled (model=%s). If Ollama is not running, rule fallback is used.", settings.ollama_model)
    elif settings.agent_provider == "openai":
        if settings.openai_api_key:
            llm = OpenAICookingAgent(
                settings.openai_api_key, settings.openai_model, settings.openai_base_url, settings.llm_timeout_seconds
            )
        else:
            log.warning("COOKING_AGENT_PROVIDER=openai but COOKING_OPENAI_API_KEY is empty → rule-based only.")
    return HybridCookingAgent(RuleBasedCookingAgent(), llm)
