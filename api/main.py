"""App factory + lifespan (ticket B1). `create_app()` is the one place
everything gets wired together: shared state, job store, auth, error
handlers, the endpoint router, and the background loops (weather hot-swap
watch, job eviction, vessel-role weather sync).

Location transparency (contract point 2): this module and everything it
wires is identical regardless of deployment target -- only `Settings`
(env vars) differs between a cloud VM and a bridge PC. `uvicorn
api.main:create_app --factory` is the same invocation either way.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import make_auth_dependency
from api.config import Settings
from api.errors import queue_full_handler, unhandled_exception_handler, value_error_handler
from api.jobs import JobStore, QueueFullError
from api.routes import router
from api.state import AppState
from api.weather_sync import WeatherSyncLoop

logger = logging.getLogger(__name__)


def create_app(config: Settings | None = None) -> FastAPI:
    config = config or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=logging.INFO)
        app_state = AppState(config)
        job_store = JobStore(app_state.executor_holder, config)
        app.state.app_state = app_state
        app.state.job_store = job_store

        # Startup readiness: confirm every worker finished loading/
        # validating its data before this process reports itself healthy
        # or accepts a real job (api/state.py's AppState.wait_until_ready
        # docstring has the full "why not a full optimise() warm-up"
        # reasoning).
        await app_state.wait_until_ready()

        await app_state.start_weather_watch()
        await job_store.start_eviction_loop()
        weather_sync = WeatherSyncLoop(config) if config.role == "vessel" else None
        if weather_sync is not None:
            await weather_sync.start()

        logger.info("stingray planner service ready (role=%s)", config.role)
        try:
            yield
        finally:
            await app_state.stop_weather_watch()
            await job_store.stop_eviction_loop()
            if weather_sync is not None:
                await weather_sync.stop()
            app_state.shutdown()

    app = FastAPI(title="Stingray Planner", lifespan=lifespan)
    # Ticket B2: the demo UI is a browser client on a different origin.
    # CORSMiddleware intercepts/answers OPTIONS preflight requests itself,
    # before they'd ever reach the router's auth dependency below --
    # preflights never carry credentials, so this is required regardless
    # of auth. allow_credentials=False deliberately: Basic Auth travels in
    # the Authorization header, not a cookie, so CORS "credentials mode"
    # (which governs cookie semantics) isn't the relevant mechanism here --
    # leaving it False keeps allow_origins free to be an explicit list
    # without the credentials=True + origins=["*"] incompatibility ever
    # coming up. allow_headers names Authorization explicitly since it
    # sits outside the CORS "simple request" header set.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(QueueFullError, queue_full_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(router, dependencies=[Depends(make_auth_dependency(config))])
    return app


# `uvicorn api.main:app` (module-level default, uses env-derived Settings)
# vs. `uvicorn api.main:create_app --factory` (tests/deploy scripts that
# need to inject a specific Settings instance) -- both work.
app = create_app()
