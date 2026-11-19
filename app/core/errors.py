import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


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


_log = logging.getLogger("app.errors")


class ServerErrorsAsJson:
    """Turns an unexpected exception into a normal JSON 500 *inside* the CORS layer.

    Without this, Starlette answers a crash from its outermost layer, after CORS, so the response has no
    Access-Control-Allow-Origin header. The browser then hides it from the web app, which can only report
    "check your internet" instead of a real error. The traceback is still logged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except Exception:
            _log.exception("unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:  # part of a response already went out; nothing sensible to replace it with
                raise
            msg = "Something went wrong on our side. Please try again."
            body = {"code": "server_error", "message": msg, "details": {}}
            await JSONResponse(status_code=500, content=body)(scope, receive, send)
