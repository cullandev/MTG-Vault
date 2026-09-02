"""Error envelope shared by every endpoint.

All failures are rendered as ``{"error": {"code", "message", "detail"}}`` so the
frontend has exactly one shape to handle (ARCHITECTURE.md section 4).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base class for errors that carry a stable machine-readable code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.detail = detail or {}


class NotFound(AppError):
    """The addressed resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Conflict(AppError):
    """The request is valid but conflicts with current state."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class Unauthorized(AppError):
    """No valid session was presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class TooManyRequests(AppError):
    """The caller is being rate limited."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "too_many_requests"


class FeatureDisabled(AppError):
    """An optional feature (AI, a meta source) is switched off."""

    status_code = status.HTTP_409_CONFLICT
    code = "feature_disabled"


def _envelope(code: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


def install_error_handlers(app: FastAPI) -> None:
    """Register the exception handlers that produce the error envelope."""

    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            413: "payload_too_large",
            429: "too_many_requests",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(
                "validation_error",
                "Request validation failed",
                {
                    "fields": [
                        {
                            "loc": [str(part) for part in err.get("loc", ())],
                            "msg": err.get("msg", ""),
                            "type": err.get("type", ""),
                        }
                        for err in exc.errors()
                    ]
                },
            ),
        )
