"""Recipe import (URL or manual text), persistence and confirmation."""

from __future__ import annotations

import uuid

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from server.app.clock import Clock, ensure_utc
from server.app.models import RecipeORM
from server.app.parsers.builder import build_recipe
from server.app.parsers.errors import RecipeParseError
from server.app.parsers.fetcher import HtmlFetcher
from server.app.parsers.jsonld import extract_jsonld_recipe
from server.app.parsers.tenk_recipe import is_tenk_url, parse_tenk_html
from server.app.parsers.text_recipe import parse_recipe_text
from server.app.repositories.recipe_repo import RecipeRepository
from server.app.schemas.recipe import (
    Recipe,
    RecipeImportRequest,
    RecipeResponse,
    RecipeSummary,
    SourceType,
)
from server.app.services.errors import NotFound


def parse_html(url: str, html: str) -> tuple[Recipe, str, list[str]]:
    warnings: list[str] = []
    if is_tenk_url(url):
        raw, parser = parse_tenk_html(html)
    else:
        raw = extract_jsonld_recipe(BeautifulSoup(html, "html.parser"))
        parser = "generic:jsonld"
        warnings.append("만개의레시피 외 사이트는 schema.org JSON-LD가 있는 경우에만 지원합니다.")
        if raw is None:
            raise RecipeParseError("이 페이지에서 schema.org Recipe 정보를 찾지 못했습니다. 텍스트 입력을 사용하세요.")
    warnings.extend(raw.warnings)
    recipe = build_recipe(raw, source_type="url", source_url=url)
    if recipe.servings is None:
        warnings.append("원본에 인분 정보가 없어 인분 변경 기능을 사용할 수 없습니다.")
    vague = [i.name for i in recipe.ingredients if i.quantity is None]
    if vague:
        warnings.append(f"정확한 수량이 없는 재료: {', '.join(vague)} (원본 표기 그대로 유지)")
    return recipe, parser, warnings


class RecipeService:
    def __init__(self, db: Session, clock: Clock, fetcher: HtmlFetcher) -> None:
        self.db = db
        self.repo = RecipeRepository(db)
        self.clock = clock
        self.fetcher = fetcher

    def import_recipe(self, req: RecipeImportRequest) -> RecipeORM:
        if req.url:
            url = str(req.url)
            html = self.fetcher.fetch(url)
            recipe, parser, warnings = parse_html(url, html)
        else:
            raw = parse_recipe_text(req.text or "", title=req.title, servings=req.servings)
            recipe = build_recipe(raw, source_type="manual", source_url=None)
            parser, warnings = "manual:text", []
        return self.save(recipe, parser, warnings)

    def save(self, recipe: Recipe, parser: str, warnings: list[str], source_type: SourceType | None = None) -> RecipeORM:
        if source_type:
            recipe = recipe.model_copy(update={"source_type": source_type})
        orm = RecipeORM(
            id=str(uuid.uuid4()),
            title=recipe.title,
            source_url=recipe.source_url,
            source_type=recipe.source_type,
            parser=parser,
            data=recipe.model_dump(mode="json"),
            confirmed=False,
            servings=recipe.servings,
            total_time_minutes=recipe.total_time_minutes,
            warnings=warnings,
            created_at=self.clock.now(),
        )
        self.repo.add(orm)
        self.db.flush()
        return orm

    def get(self, recipe_id: str) -> RecipeORM:
        recipe = self.repo.get(recipe_id)
        if recipe is None:
            raise NotFound(f"레시피를 찾을 수 없습니다: {recipe_id}")
        return recipe

    def confirm(self, recipe_id: str) -> RecipeORM:
        recipe = self.get(recipe_id)
        recipe.confirmed = True
        return recipe

    def list(self, query: str | None = None) -> list[RecipeORM]:
        return self.repo.list(query)


def to_recipe_response(orm: RecipeORM) -> RecipeResponse:
    return RecipeResponse(
        recipe_id=orm.id,
        parser=orm.parser,
        confirmed=orm.confirmed,
        warnings=list(orm.warnings or []),
        created_at=ensure_utc(orm.created_at),
        recipe=Recipe.model_validate(orm.data),
    )


def to_recipe_summary(orm: RecipeORM) -> RecipeSummary:
    return RecipeSummary(
        recipe_id=orm.id, title=orm.title, source_type=orm.source_type, source_url=orm.source_url,
        confirmed=orm.confirmed, created_at=ensure_utc(orm.created_at),
    )
