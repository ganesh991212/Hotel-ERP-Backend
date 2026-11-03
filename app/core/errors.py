"""One error envelope for every failure: ``{"code", "message", "details"}``.

Clients branch on ``code`` (stable, upper snake case), never on ``message`` (human text
that may change or be localised).
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def unauthenticated(code: str = "UNAUTHENTICATED", message: str = "Not authenticated") -> AppError:
    return AppError(
        status.HTTP_401_UNAUTHORIZED, code, message, headers={"WWW-Authenticate": "Bearer"}
    )


def forbidden(code: str = "FORBIDDEN", message: str = "Insufficient permissions") -> AppError:
    return AppError(status.HTTP_403_FORBIDDEN, code, message)


def not_found(message: str = "Not found") -> AppError:
    # 404, never 403, for rows in another hotel: a 403 confirms the row exists.
    return AppError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", message)


def conflict(code: str, message: str, details: object | None = None) -> AppError:
    return AppError(status.HTTP_409_CONFLICT, code, message, details)


def unprocessable(code: str, message: str, details: object | None = None) -> AppError:
    return AppError(422, code, message, details)


def _body(code: str, message: str, details: object | None) -> dict[str, object]:
    return {"code": code, "message": message, "details": details}


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body("VALIDATION_ERROR", "Request is invalid", details),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            _body(code, str(exc.detail), None),
            status_code=exc.status_code,
            headers=exc.headers,
        )
