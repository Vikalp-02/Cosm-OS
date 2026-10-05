"""The API application."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from cosmos.app.api import router
from cosmos.app.db import make_engine, make_session_factory
from cosmos.app.security import LoginThrottle
from cosmos.app.settings import Settings

log = logging.getLogger("cosmos.api")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="Cosmos",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.sessions = make_session_factory(engine)
    app.state.throttle = LoginThrottle(settings.login_attempts, settings.login_window_minutes * 60)
    allowed_origins = frozenset(settings.allowed_origins)

    @app.middleware("http")
    async def guard_and_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()

        # The session cookie rides along on any request a browser makes, so a
        # request that changes something must come from one of our own pages.
        # Browsers always name the origin on such requests; other clients do
        # not, and they cannot be tricked into sending someone else's cookie.
        origin = request.headers.get("origin")
        if request.method not in _SAFE_METHODS and origin and origin not in allowed_origins:
            response: Response = JSONResponse(
                {"detail": "This request came from a site that is not allowed."},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                log.exception(
                    "request %s failed: %s %s", request_id, request.method, request.url.path
                )
                response = JSONResponse(
                    {"detail": "Something went wrong on our side. Please try again."},
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        response.headers["X-Request-ID"] = request_id
        log.info(
            "%s %s -> %d in %.0f ms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response

    app.include_router(router)
    return app
