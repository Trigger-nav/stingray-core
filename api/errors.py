"""Exception handling (ticket B1 design 10): `core.optimiser._validate_endpoint`
(called from `PlanRequest.__post_init__`) and `core.geography.
OutOfOperatingAreaError` (a `ValueError` subclass) both raise bare
`ValueError`s with already-descriptive messages -- this module maps those
to `422` responses reusing that message, entirely in `api/`. `core/`/
`ingest/` stay completely untouched; no exception-handling awareness is
added there.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from api.jobs import QueueFullError

logger = logging.getLogger(__name__)


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"code": "invalid_request", "message": str(exc)},
    )


async def queue_full_handler(request: Request, exc: QueueFullError) -> JSONResponse:
    """Ticket B2 amendment -- `JobStore.submit()`'s queue-depth cap.
    `Retry-After` is a rough, fixed hint (not derived from actual job
    durations, which vary 1.5-60s per CLAUDE.md's B1 gotcha) -- good
    enough for a client backoff, not a precise estimate."""
    return JSONResponse(
        status_code=429,
        content={"code": "queue_full", "message": str(exc)},
        headers={"Retry-After": "5"},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception in %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "internal error -- see server logs"},
    )
