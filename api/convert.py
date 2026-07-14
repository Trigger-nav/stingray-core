"""Hand-written dataclass<->pydantic conversion (ticket B1). Kept separate
from api/schemas.py so tests/test_api_schema_parity.py can introspect
schema *shape* without conversion-function noise, and so pydantic's
validation-on-construction benefit isn't lost behind a generic
dataclasses.asdict() round-trip on the runtime path (asdict() is only used
inside the parity test's own introspection, never here).
"""

from __future__ import annotations

from api.schemas import (
    AddedResistanceCoefficientsModel,
    AlterationModel,
    CalmResistanceCurveModel,
    CandidateModel,
    ComfortCoefficientsModel,
    EngineSpecModel,
    HullParticularsModel,
    LatLonModel,
    LegTargetModel,
    PlanRequestIn,
    PlanResultOut,
    PruneDiagnosticModel,
    VesselSpecModel,
    WearPolicyModel,
    WeightsModel,
)
from core.geography import Geography
from core.optimiser import (
    Alteration,
    Candidate,
    LegTarget,
    PlanRequest,
    PlanResult,
    PruneDiagnostic,
)
from core.regionpack import RegionPack, resolve_pack_endpoint
from core.units import LatLon
from core.vessel_spec import (
    AddedResistanceCoefficients,
    CalmResistanceCurve,
    ComfortCoefficients,
    EngineSpec,
    HullParticulars,
    VesselSpec,
    WearPolicy,
)
from core.weather import WeatherField
from core.weights import Weights


def latlon_from_model(m: LatLonModel) -> LatLon:
    return LatLon(lat_deg=m.lat_deg, lon_deg=m.lon_deg)


def latlon_to_model(p: LatLon) -> LatLonModel:
    return LatLonModel(lat_deg=p.lat_deg, lon_deg=p.lon_deg)


def vessel_spec_from_model(m: VesselSpecModel) -> VesselSpec:
    return VesselSpec(
        name=m.name,
        hull=HullParticulars(**m.hull.model_dump()),
        calm_resistance=CalmResistanceCurve(**m.calm_resistance.model_dump()),
        added_resistance=AddedResistanceCoefficients(**m.added_resistance.model_dump()),
        engines=tuple(EngineSpec(**e.model_dump()) for e in m.engines),
        hotel_load_fuel_kg_per_h=m.hotel_load_fuel_kg_per_h,
        fuel_density_kg_per_l=m.fuel_density_kg_per_l,
        co2_per_kg_fuel=m.co2_per_kg_fuel,
        comfort=ComfortCoefficients(**m.comfort.model_dump()),
        wear_policy=WearPolicy(**m.wear_policy.model_dump()),
        min_under_keel_clearance_m=m.min_under_keel_clearance_m,
        provisional=m.provisional,
    )


def vessel_spec_to_model(spec: VesselSpec) -> VesselSpecModel:
    return VesselSpecModel(
        name=spec.name,
        hull=HullParticularsModel(**vars(spec.hull)),
        calm_resistance=CalmResistanceCurveModel(**vars(spec.calm_resistance)),
        added_resistance=AddedResistanceCoefficientsModel(**vars(spec.added_resistance)),
        engines=[EngineSpecModel(**vars(e)) for e in spec.engines],
        hotel_load_fuel_kg_per_h=spec.hotel_load_fuel_kg_per_h,
        fuel_density_kg_per_l=spec.fuel_density_kg_per_l,
        co2_per_kg_fuel=spec.co2_per_kg_fuel,
        comfort=ComfortCoefficientsModel(**vars(spec.comfort)),
        wear_policy=WearPolicyModel(**vars(spec.wear_policy)),
        min_under_keel_clearance_m=spec.min_under_keel_clearance_m,
        provisional=spec.provisional,
    )


def plan_request_from_in(
    body: PlanRequestIn,
    *,
    default_vessel: VesselSpec,
    weather: WeatherField,
    geography: Geography,
    region_pack: RegionPack,
) -> PlanRequest:
    """`weather`/`geography` are the server's shared singleton state
    (api/state.py) -- never client-supplied, see PlanRequestIn's docstring.
    `region_pack` is the object already resolved from `body.pack_id` by
    the caller (api/routes.py, via `AppState`'s per-pack dicts -- ticket
    R1) -- this function stays a pure conversion, with no AppState-shaped
    lookup logic of its own. Omitted origin/destination fall back to
    `region_pack.default_origin`/`.default_destination` -- for the "med"
    pack these are exactly `DEFAULT_ORIGIN`/`DEFAULT_DESTINATION`, so an
    omitted field behaves identically to before this ticket for every
    existing caller; a pack with no configured default raises rather than
    silently falling back to the Med's endpoints."""
    origin, destination = resolve_pack_endpoint(
        region_pack,
        latlon_from_model(body.origin) if body.origin is not None else None,
        latlon_from_model(body.destination) if body.destination is not None else None,
    )
    return PlanRequest(
        weather=weather,
        geography=geography,
        vessel=vessel_spec_from_model(body.vessel) if body.vessel is not None else default_vessel,
        pace=body.pace,
        comfort=body.comfort,
        origin=origin,
        destination=destination,
        origin_is_anchorage=body.origin_is_anchorage,
        destination_is_anchorage=body.destination_is_anchorage,
        region_pack=region_pack,
        latest_arrival_h=body.latest_arrival_h,
        departure_t0_h=body.departure_t0_h,
        speeds_kn=tuple(body.speeds_kn) if body.speeds_kn is not None else None,
    )


def leg_target_to_model(lt: LegTarget) -> LegTargetModel:
    return LegTargetModel(
        from_point=latlon_to_model(lt.from_point),
        to_point=latlon_to_model(lt.to_point),
        course_deg=lt.course_deg,
        cts_deg=lt.cts_deg,
        target_stw_kn=lt.target_stw_kn,
        eta_h=lt.eta_h,
    )


def alteration_to_model(a: Alteration) -> AlterationModel:
    return AlterationModel(
        position=latlon_to_model(a.position),
        time_h=a.time_h,
        new_cts_deg=a.new_cts_deg,
    )


def candidate_to_model(c: Candidate) -> CandidateModel:
    return CandidateModel(
        corridor_name=c.corridor_name,
        side=c.side,
        speed_kn=c.speed_kn,
        active_engines=c.active_engines,
        track=[latlon_to_model(p) for p in c.track],
        duration_h=c.duration_h,
        distance_nm=c.distance_nm,
        fuel_kg=c.fuel_kg,
        comfort_index=c.comfort_index,
        wear_index=c.wear_index,
        max_hs_m=c.max_hs_m,
        score_eur=c.score_eur,
        meets_eta_window=c.meets_eta_window,
        leg_targets=[leg_target_to_model(lt) for lt in c.leg_targets],
        alteration_list=[alteration_to_model(a) for a in c.alteration_list],
    )


def prune_diagnostic_to_model(d: PruneDiagnostic) -> PruneDiagnosticModel:
    return PruneDiagnosticModel(
        code=d.code,
        message=d.message,
        side=d.side,
        speed_kn=d.speed_kn,
        active_engines=d.active_engines,
    )


def weights_to_model(w: Weights) -> WeightsModel:
    return WeightsModel(**vars(w))


def plan_result_to_model(result: PlanResult) -> PlanResultOut:
    return PlanResultOut(
        candidates=[candidate_to_model(c) for c in result.candidates],
        baseline=candidate_to_model(result.baseline),
        weights=weights_to_model(result.weights),
        missed_window=result.missed_window,
        baseline_provisional=result.baseline_provisional,
        diagnostics=[prune_diagnostic_to_model(d) for d in result.diagnostics],
    )
