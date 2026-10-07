"""Typed exceptions + FastAPI handlers (uniform JSON error envelope)."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("api")


class APIError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(APIError):
    status = 404
    code = "not_found"


class BadRequest(APIError):
    status = 400
    code = "bad_request"


class DataMissing(APIError):
    status = 503
    code = "data_missing"


def install(app) -> None:
    @app.exception_handler(APIError)
    async def _handle(_: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "message": exc.message,
                     "details": exc.details},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error",
                     "message": "Unexpected server error", "details": {}},
        )
