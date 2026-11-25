from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from server.app.parsers.errors import RecipeImportError
from server.app.schemas.base import ErrorResponse
from server.app.services.errors import DomainError


def _json(status: int, error: str, message: str, details: dict[str, object] | None = None) -> JSONResponse:
    body = ErrorResponse(error=error, message=message, details=details).model_dump(by_alias=True, exclude_none=True)
    return JSONResponse(status_code=status, content=body)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        details = dict(exc.details or {})
        code = str(details.pop("code", exc.code))
        return _json(exc.status_code, code, exc.message, details or None)

    @app.exception_handler(RecipeImportError)
    async def _import(_: Request, exc: RecipeImportError) -> JSONResponse:
        return _json(
            exc.status_code, exc.code, exc.message,
            {"fallback": "POST /api/recipes/import 에 {\"text\": \"...\"} 형식으로 레시피를 직접 입력할 수 있습니다."},
        )

    @app.exception_handler(StaleDataError)
    async def _stale(_: Request, exc: StaleDataError) -> JSONResponse:
        return _json(409, "VERSION_CONFLICT", "다른 요청이 먼저 세션을 변경했습니다. 다시 시도하세요.")
