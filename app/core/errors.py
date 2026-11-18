from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Error rendered as {code, message, details} (docs/06-api-surface.md)."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def not_found(entity: str) -> ApiError:
    return ApiError(404, "not_found", f"{entity} not found")


def forbidden(code: str = "forbidden", message: str = "Not allowed", details: dict | None = None) -> ApiError:
    return ApiError(403, code, message, details)


def conflict(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(409, code, message, details)


def bad_request(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(400, code, message, details)


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"code": exc.code, "message": exc.message, "details": exc.details}
    )
