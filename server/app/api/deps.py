"""Application container + per-request unit of work (one DB transaction per request)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from server.app.agents.hybrid import HybridCookingAgent
from server.app.clock import Clock
from server.app.config import Settings
from server.app.parsers.fetcher import HtmlFetcher


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    clock: Clock
    agent: HybridCookingAgent
    fetcher: HtmlFetcher

    @contextmanager
    def uow(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]
