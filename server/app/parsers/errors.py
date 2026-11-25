from __future__ import annotations


class RecipeImportError(Exception):
    """Base error for import failures. ``code`` is surfaced to API clients."""

    code = "RECIPE_IMPORT_FAILED"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RecipeFetchError(RecipeImportError):
    code = "RECIPE_FETCH_FAILED"
    status_code = 502


class RecipeFetchForbidden(RecipeImportError):
    code = "RECIPE_FETCH_DISALLOWED"
    status_code = 403


class RecipeParseError(RecipeImportError):
    code = "RECIPE_PARSE_FAILED"
    status_code = 422
