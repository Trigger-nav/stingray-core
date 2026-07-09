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

from api.auth import make_auth_dependency
from api.config import Settings
from api.errors import unhandled_exception_handler, value_error_handler
from api.jobs import JobStore
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
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(router, dependencies=[Depends(make_auth_dependency(config))])
    return app


# `uvicorn api.main:app` (module-level default, uses env-derived Settings)
# vs. `uvicorn api.main:create_app --factory` (tests/deploy scripts that
# need to inject a specific Settings instance) -- both work.
app = create_app()
