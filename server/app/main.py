"""FastAPI entrypoint: ``uvicorn server.app.main:app --reload``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from server.app.agents.factory import build_agent
from server.app.agents.ollama_agent import OllamaCookingAgent
from server.app.api import recipes, sessions
from server.app.api.deps import Container
from server.app.api.errors import register_error_handlers
from server.app.clock import Clock
from server.app.config import Settings, get_settings
from server.app.db import create_db_engine, init_db, make_session_factory
from server.app.parsers.fetcher import HtmlFetcher


def build_container(settings: Settings, clock: Clock | None = None, fetcher: HtmlFetcher | None = None) -> Container:
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    return Container(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        clock=clock or Clock(),
        agent=build_agent(settings),
        fetcher=fetcher
        or HtmlFetcher(
            user_agent=settings.user_agent, timeout=settings.fetch_timeout_seconds, respect_robots=settings.respect_robots_txt
        ),
    )


def create_app(container: Container | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        app.state.container.engine.dispose()

    app = FastAPI(
        title="Voice Cooking Assistant API",
        version="0.1.0",
        description="레시피 URL 수집 → Cooking Session 실행 → 음성/텍스트 명령 처리. 기본은 규칙 기반 에이전트(오프라인).",
        lifespan=lifespan,
    )
    app.state.container = container or build_container(get_settings())
    register_error_handlers(app)
    app.include_router(recipes.router)
    app.include_router(sessions.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, object]:
        c: Container = app.state.container
        with c.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        llm = c.agent.llm
        llm_status: str = "disabled"
        if isinstance(llm, OllamaCookingAgent):
            llm_status = "ready" if llm.is_available() else f"unavailable (model {llm.model} not found; rule fallback)"
        elif llm is not None:
            llm_status = "configured"
        return {"status": "ok", "db": "ok", "agent": c.agent.name, "llm": llm_status}

    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Lazily build ``app`` so importing this module (e.g. in tests) has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
