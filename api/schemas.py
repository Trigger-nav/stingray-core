"""Pydantic mirrors of core/'s frozen dataclasses (ticket B1, contract
point 3). Hand-written field-for-field, not derived -- core/ has no
to_dict/serializer to derive from. `tests/test_api_schema_parity.py` is
what keeps these honest against core/ over time (introspects both field
sets and fails CI on undeclared drift), not developer discipline alone.

Deliberately does not mirror `core.geography.Geography`/
`core.weather.WeatherField` -- those are server-side singleton state
(api/state.py), never client-supplied payloads.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Bumped on payload-shape changes (independent of the /v1 URL prefix, which
# bumps on breaking route/behaviour changes) -- surfaced on GET /v1/health.
SCHEMA_VERSION = "1.0.0"


class LatLonModel(BaseModel):
    lat_deg: float
    lon_deg: float


# --- VesselSpec mirror (core/vessel_spec.py) --------------------------------


class HullParticularsModel(BaseModel):
    length_wl_m: float
    beam_wl_m: float
    block_coefficient: float
    draft_m: float


class CalmResistanceCurveModel(BaseModel):
    linear_coefficient: float
    cubic_coefficient: float
    steepening_coefficient: float
    steepening_exponent: float
    hull_speed_froude: float


class AddedResistanceCoefficientsModel(BaseModel):
    scale: float
    period_reference_s: float
    head_factor: float
    following_factor: float


class EngineSpecModel(BaseModel):
    name: str
    mcr_kw: float
    sfoc_base_g_per_kwh: float
    sfoc_min_load_fraction: float
    sfoc_curvature: float


class ComfortCoefficientsModel(BaseModel):
    scale: float
    hs_exponent: float
    beam_base: float
    beam_amplitude: float
    head_bonus: float
    head_bonus_threshold_deg: float
    period_reference_s: float
    speed_base: float
    speed_scale_kn: float


class WearPolicyModel(BaseModel):
    weight_eur_equivalent: float
    max_continuous_load_fraction: float
    slamming_hs_threshold_m: float
    slamming_min_speed_ms: float
    slamming_encounter_angle_deg: float
    load_wear_scale: float
    slam_wear_scale: float
    single_engine_wear_bonus: float
    load_cycling_limit: float | None = None


class VesselSpecModel(BaseModel):
    name: str
    hull: HullParticularsModel
    calm_resistance: CalmResistanceCurveModel
    added_resistance: AddedResistanceCoefficientsModel
    engines: list[EngineSpecModel]
    hotel_load_fuel_kg_per_h: float
    fuel_density_kg_per_l: float
    co2_per_kg_fuel: float
    comfort: ComfortCoefficientsModel
    wear_policy: WearPolicyModel
    min_under_keel_clearance_m: float
    provisional: bool = True


# --- PlanRequest / PlanResult mirrors (core/optimiser.py) -------------------


class PlanRequestIn(BaseModel):
    """Mirrors `core.optimiser.PlanRequest` minus `weather`/`geography`
    (server-side singleton state, never a client payload -- api/state.py).
    `origin`/`destination` default to `None` so an omitted field behaves
    identically to core's own `DEFAULT_ORIGIN`/`DEFAULT_DESTINATION`
    defaulting, applied in `api/convert.py`, not here (pydantic can't call
    into core/ for a default)."""

    pace: float
    comfort: float
    # Ticket R1: which RegionPack this request targets -- resolved to a
    # core.regionpack.RegionPack in api/convert.py (a string key, not the
    # pack object itself, since PlanRequestIn is a client-facing wire
    # schema; core.optimiser.PlanRequest's matching field is `region_pack`,
    # not `pack_id` -- see tests/test_api_schema_parity.py's PAIRS entry
    # for this pair). Default "med" preserves exact current behaviour for
    # every existing caller (the hosted demo's fixed passage).
    pack_id: str = "med"
    origin: LatLonModel | None = None
    destination: LatLonModel | None = None
    origin_is_anchorage: bool = False
    destination_is_anchorage: bool = False
    latest_arrival_h: float | None = None
    departure_t0_h: float = 0.0
    speeds_kn: list[float] | None = None
    # If omitted, the server uses its loaded default VesselSpec; if
    # supplied, converted and used for this job only -- forward-compatible
    # with per-vessel requests without building real multi-tenancy now.
    vessel: VesselSpecModel | None = None


class LegTargetModel(BaseModel):
    from_point: LatLonModel
    to_point: LatLonModel
    course_deg: float
    cts_deg: float
    target_stw_kn: float
    eta_h: float


class AlterationModel(BaseModel):
    position: LatLonModel
    time_h: float
    new_cts_deg: float


class CandidateModel(BaseModel):
    corridor_name: str
    # str | None (ticket R1): core._route_signature returns None for a
    # pack/passage with no Corsica-like distinguishing region -- see
    # core/optimiser.py's docstring.
    side: str | None
    speed_kn: float
    active_engines: int
    track: list[LatLonModel]
    duration_h: float
    distance_nm: float
    fuel_kg: float
    comfort_index: float
    wear_index: float
    max_hs_m: float
    score_eur: float
    meets_eta_window: bool | None
    leg_targets: list[LegTargetModel]
    alteration_list: list[AlterationModel]


class PruneDiagnosticModel(BaseModel):
    code: str
    message: str
    side: str | None = None
    speed_kn: float | None = None
    active_engines: int | None = None


class WeightsModel(BaseModel):
    fuel_eur_per_kg: float
    time_eur_per_min: float
    comfort_eur_per_index_point: float
    wear_eur_per_index_point: float


class PlanResultOut(BaseModel):
    candidates: list[CandidateModel]
    baseline: CandidateModel
    weights: WeightsModel
    missed_window: bool
    baseline_provisional: bool = True
    diagnostics: list[PruneDiagnosticModel] = Field(default_factory=list)


# --- Job envelope (api/jobs.py) -- not a core/ mirror, native to the API ----

JobStatus = Literal["queued", "running", "done", "failed"]


class JobErrorModel(BaseModel):
    code: Literal["invalid_request", "internal_error"]
    message: str


class JobRecordOut(BaseModel):
    job_id: str
    status: JobStatus
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: PlanResultOut | None = None
    error: JobErrorModel | None = None


class JobSubmittedOut(BaseModel):
    job_id: str
    status: JobStatus


# --- Misc read-only surfaces -------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"]
    schema_version: str
    role: Literal["cloud", "vessel"]
    weather_source: str | None = None
    weather_cycle: str | None = None
    weather_fetched: str | None = None


class TelemetryStatusOut(BaseModel):
    """Read-only view into capture/'s local store (design 4/8) -- the
    planner process reads the same SQLite file capture/service.py writes,
    no in-process call between the two services."""

    last_sample_at: float | None
    sensor_tier: str | None
    sample_count: int
    gap_seconds: float | None


class FavouriteIn(BaseModel):
    """Ticket R1: POST /v1/favourites body. vessel_id is a query param
    (see api/routes.py), not part of this body -- the identity scoping a
    favourite belongs to is the same shape auth would eventually use, not
    request-payload data the client picks per-call."""

    name: str
    lat_deg: float
    lon_deg: float
    is_anchorage: bool = False
    pack_id: str = "med"


class FavouriteOut(BaseModel):
    id: str
    vessel_id: str
    name: str
    lat_deg: float
    lon_deg: float
    is_anchorage: bool
    pack_id: str
    created_at: float


class WeatherFieldOut(BaseModel):
    """Ticket B2 amendment: a downsampled weather-grid snapshot at one
    valid-time, for the demo UI's chart heatmap/wind layer (`drawWx()`,
    the third `SCENARIOS`-call-site the original B2 plan missed).
    Deliberately not a mirror of `core.weather.GriddedWeatherField`'s
    full-resolution grid -- this is a display-only downsample built by
    repeatedly calling the same `.sample()` interpolation every other
    consumer uses, not a new `core/` code path. `hs_m`/`wind_from_deg` are
    `null` (never a Python `NaN`, which isn't valid JSON) over land/masked
    cells, matching `core/weather.py`'s own "missing propagates as
    missing" convention -- the client should skip drawing a null cell,
    not treat it as calm."""

    lat0_deg: float
    dlat_deg: float
    lon0_deg: float
    dlon_deg: float
    nlat: int
    nlon: int
    valid_time_h: float  # quantized to the nearest hour -- see api/weather_field.py
    hs_m: list[list[float | None]]  # [lat_idx][lon_idx]
    wind_speed_kn: list[list[float | None]]
    wind_from_deg: list[list[float | None]]
