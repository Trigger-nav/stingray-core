"""Endpoint surface (ticket B1 design 4). Interactive planning only -- see
api/__init__.py's module docstring: no telemetry write/ingest endpoint of
any kind belongs here, ever (contract point 4).
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from api.convert import (
    latlon_from_model,
    plan_request_from_in,
    plan_result_to_model,
    vessel_spec_from_model,
    vessel_spec_to_model,
)
from api.jobs import JobStore
from api.schemas import (
    SCHEMA_VERSION,
    HealthOut,
    JobErrorModel,
    JobRecordOut,
    JobSubmittedOut,
    PlanRequestIn,
    TelemetryStatusOut,
    VesselSpecModel,
)
from api.state import AppState, PlanJobPayload
from api.weather_field import build_weather_field, compute_weather_field_etag, quantize_hour

router = APIRouter(prefix="/v1")


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state


def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


AppStateDep = Annotated[AppState, Depends(get_app_state)]
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]


@router.post("/plans", status_code=202, response_model=JobSubmittedOut)
def submit_plan(
    body: PlanRequestIn, app_state: AppStateDep, job_store: JobStoreDep
) -> JobSubmittedOut:
    """Fail-fast: `PlanRequest.__post_init__` (navigability/anchorage-depth
    validation) runs synchronously here, against the main process's own
    `app_state.geography` -- an invalid request never occupies a worker
    slot at all (design 10). A `ValueError` here is caught by
    `api/errors.py`'s handler and turned into a `422`."""
    # Constructing the full PlanRequest validates the request (raises
    # ValueError on failure) without needing its own weather/geography for
    # anything beyond that validation -- the actual optimise() call in the
    # worker reconstructs a fresh PlanRequest from the payload against
    # *that worker's* geography/weather (api/state.py's run_plan_job).
    plan_request_from_in(
        body,
        default_vessel=app_state.vessel,
        weather=app_state.weather,
        geography=app_state.geography,
    )
    payload = PlanJobPayload(
        pace=body.pace,
        comfort=body.comfort,
        origin=latlon_from_model(body.origin) if body.origin is not None else None,
        destination=latlon_from_model(body.destination) if body.destination is not None else None,
        origin_is_anchorage=body.origin_is_anchorage,
        destination_is_anchorage=body.destination_is_anchorage,
        latest_arrival_h=body.latest_arrival_h,
        departure_t0_h=body.departure_t0_h,
        speeds_kn=tuple(body.speeds_kn) if body.speeds_kn is not None else None,
        vessel_override=vessel_spec_from_model(body.vessel) if body.vessel is not None else None,
    )
    record = job_store.submit(payload)
    return JobSubmittedOut(job_id=record.job_id, status=record.status)


@router.get("/plans/{job_id}", response_model=JobRecordOut)
def get_plan(job_id: str, job_store: JobStoreDep) -> JobRecordOut:
    record = job_store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return JobRecordOut(
        job_id=record.job_id,
        status=record.status,
        submitted_at=record.submitted_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        result=plan_result_to_model(record.result) if record.result is not None else None,
        error=(
            JobErrorModel(code=record.error.code, message=record.error.message)
            if record.error is not None
            else None
        ),
    )


@router.get("/health", response_model=HealthOut)
def health(app_state: AppStateDep) -> HealthOut:
    return HealthOut(
        status="ok",
        schema_version=SCHEMA_VERSION,
        role=app_state.config.role,
        weather_source=app_state.weather.source,
        weather_cycle=app_state.weather.cycle,
        weather_fetched=app_state.weather.fetched,
    )


@router.get("/vessel", response_model=VesselSpecModel)
def get_vessel(app_state: AppStateDep) -> VesselSpecModel:
    return vessel_spec_to_model(app_state.vessel)


@router.get("/weather/latest.npz")
def get_latest_weather(app_state: AppStateDep) -> FileResponse:
    """Cloud role only (design 6) -- serves the current compact npz for
    vessel-role instances to pull. `FileResponse` sets `Last-Modified`
    from the file's own mtime automatically, enabling the vessel-side
    sync task's conditional `If-Modified-Since` GET without extra code
    here."""
    if app_state.config.role != "cloud":
        raise HTTPException(
            status_code=404, detail="weather distribution is only served by cloud-role instances"
        )
    path = app_state.config.weather_npz_path
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no weather file available yet")
    return FileResponse(path, media_type="application/octet-stream", filename="latest.npz")


@router.get("/weather/field")
def get_weather_field(request: Request, app_state: AppStateDep, h: float = 0.0) -> Response:
    """Ticket B2 amendment: a downsampled weather-grid snapshot at one
    valid-time, for the demo UI's chart heatmap/wind layer (`drawWx()`)
    and forecast scrub. `h` is quantized to the nearest hour (source
    weather data's own real resolution, per `api/weather_field.py`) both
    for the served content and the `ETag`, so a continuous scrub gesture
    collapses onto a small number of distinct, cacheable responses rather
    than recomputing/re-transferring a fresh grid on every tick."""
    valid_time_h = quantize_hour(h)
    etag = compute_weather_field_etag(app_state.weather, valid_time_h)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    field = build_weather_field(app_state.weather, valid_time_h)
    return JSONResponse(content=field.model_dump(), headers={"ETag": etag})


@router.get("/telemetry/status", response_model=TelemetryStatusOut)
def get_telemetry_status(app_state: AppStateDep) -> TelemetryStatusOut:
    """Read-only view into capture/'s local store (design 4/8) -- reads
    the same SQLite file capture/service.py writes, in a separate OS
    process; no in-process call between the two services."""
    from capture.store import query_status

    status = query_status(app_state.config.telemetry_db_path)
    return TelemetryStatusOut(
        last_sample_at=status.last_sample_at,
        sensor_tier=status.sensor_tier,
        sample_count=status.sample_count,
        gap_seconds=status.gap_seconds,
    )
