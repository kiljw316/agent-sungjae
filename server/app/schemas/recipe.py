from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from server.app.schemas.base import ApiModel

SourceType = Literal["url", "manual", "fixture"]


class Ingredient(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    canonical: str = Field(min_length=1, max_length=100)
    raw_text: str
    quantity: float | None = None  # numeric amount when present in the source
    quantity_text: str | None = None  # amount as written ("1/3", "반")
    unit: str | None = None
    note: str | None = None  # "약간", "기호에맞게" ...
    group: str | None = None


class RecipeStep(ApiModel):
    position: int = Field(ge=1)
    text: str = Field(min_length=1)
    ingredient_refs: list[str] = Field(default_factory=list)
    # Only filled when the source text explicitly states minutes. Never invented.
    duration_minutes: int | None = None


class Recipe(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    source_url: str | None = None
    source_type: SourceType
    servings: float | None = None
    total_time_minutes: int | None = None
    description: str | None = Field(default=None, max_length=300)
    ingredients: list[Ingredient] = Field(min_length=1)
    steps: list[RecipeStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _positions_are_sequential(self) -> Recipe:
        positions = [s.position for s in self.steps]
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError("step positions must be 1..N in order")
        return self


class RecipeImportRequest(ApiModel):
    """Either ``url`` (fetch & parse) or ``text`` (manual fallback) must be given."""

    url: HttpUrl | None = None
    text: str | None = Field(default=None, max_length=20000)
    title: str | None = Field(default=None, max_length=300)
    servings: float | None = Field(default=None, gt=0, le=50)

    @model_validator(mode="after")
    def _one_source(self) -> RecipeImportRequest:
        if bool(self.url) == bool(self.text):
            raise ValueError("provide exactly one of 'url' or 'text'")
        return self


class RecipeResponse(ApiModel):
    recipe_id: str
    parser: str
    confirmed: bool
    warnings: list[str]
    created_at: datetime
    recipe: Recipe


class RecipeSummary(ApiModel):
    recipe_id: str
    title: str
    source_type: SourceType
    source_url: str | None
    confirmed: bool
    created_at: datetime
