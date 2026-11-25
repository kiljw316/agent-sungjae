"""Server settings loaded from environment variables / .env (prefix ``COOKING_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COOKING_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/cooking.db"

    # Agent provider. "rule" works fully offline and is the default.
    agent_provider: Literal["rule", "ollama", "openai"] = "rule"
    llm_timeout_seconds: float = 20.0

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # Recipe fetching
    fetch_timeout_seconds: float = 15.0
    respect_robots_txt: bool = True
    user_agent: str = "VoiceCookingAssistant/0.1 (personal MVP; single-page fetch)"

    # Conversation policy
    auto_apply_explicit_exclusion: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
