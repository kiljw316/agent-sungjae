from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from server.app.api.deps import Container, get_container
from server.app.schemas.base import ErrorResponse
from server.app.schemas.recipe import RecipeImportRequest, RecipeResponse, RecipeSummary
from server.app.services.recipe_service import (
    RecipeService,
    to_recipe_response,
    to_recipe_summary,
)

router = APIRouter(prefix="/api/recipes", tags=["recipes"])
C = Annotated[Container, Depends(get_container)]
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 502: {"model": ErrorResponse}}


@router.post("/import", response_model=RecipeResponse, status_code=status.HTTP_201_CREATED, responses=ERRORS)
def import_recipe(body: RecipeImportRequest, c: C) -> RecipeResponse:
    """Fetch & parse a recipe URL (JSON-LD first, HTML fallback) or parse pasted text."""
    with c.uow() as db:
        orm = RecipeService(db, c.clock, c.fetcher).import_recipe(body)
        return to_recipe_response(orm)


@router.get("", response_model=list[RecipeSummary])
def list_recipes(c: C, q: Annotated[str | None, Query(max_length=100)] = None) -> list[RecipeSummary]:
    with c.uow() as db:
        return [to_recipe_summary(r) for r in RecipeService(db, c.clock, c.fetcher).list(q)]


@router.get("/{recipe_id}", response_model=RecipeResponse, responses=ERRORS)
def get_recipe(recipe_id: str, c: C) -> RecipeResponse:
    with c.uow() as db:
        return to_recipe_response(RecipeService(db, c.clock, c.fetcher).get(recipe_id))


@router.post("/{recipe_id}/confirm", response_model=RecipeResponse, responses=ERRORS)
def confirm_recipe(recipe_id: str, c: C) -> RecipeResponse:
    """User reviewed the extracted recipe. Required before a cooking session can be created."""
    with c.uow() as db:
        return to_recipe_response(RecipeService(db, c.clock, c.fetcher).confirm(recipe_id))
