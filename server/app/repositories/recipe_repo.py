from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.models import RecipeORM


class RecipeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, recipe: RecipeORM) -> RecipeORM:
        self.db.add(recipe)
        return recipe

    def get(self, recipe_id: str) -> RecipeORM | None:
        return self.db.get(RecipeORM, recipe_id)

    def list(self, query: str | None = None, limit: int = 50) -> list[RecipeORM]:
        stmt = select(RecipeORM).order_by(RecipeORM.created_at.desc()).limit(limit)
        if query:
            stmt = stmt.where(RecipeORM.title.contains(query))
        return list(self.db.scalars(stmt))
