"""Drift-detection mechanism for ticket B1 contract point 3: core/'s frozen
dataclasses are the source of truth, api/schemas.py's pydantic models are
hand-written mirrors -- this test is what makes that "stays honest
automatically" rather than "developer remembers to update both". A field
added to a core dataclass without a matching pydantic-mirror update fails
this test, in CI, not silently.

Deliberately checks field-NAME sets only, not full type-object equality --
JSON/pydantic has no tuple type (core's `tuple[...]` becomes pydantic's
`list[...]`) and that divergence is intentional, not drift.
"""

import dataclasses

from api import schemas
from core import optimiser, units, vessel_spec, weights

# (core dataclass, pydantic model, {core-only field names that are a
# deliberate, reviewed omission -- not accidental drift}).
PAIRS = [
    (units.LatLon, schemas.LatLonModel, set()),
    (vessel_spec.HullParticulars, schemas.HullParticularsModel, set()),
    (vessel_spec.CalmResistanceCurve, schemas.CalmResistanceCurveModel, set()),
    (
        vessel_spec.AddedResistanceCoefficients,
        schemas.AddedResistanceCoefficientsModel,
        set(),
    ),
    (vessel_spec.EngineSpec, schemas.EngineSpecModel, set()),
    (vessel_spec.ComfortCoefficients, schemas.ComfortCoefficientsModel, set()),
    (vessel_spec.WearPolicy, schemas.WearPolicyModel, set()),
    (vessel_spec.VesselSpec, schemas.VesselSpecModel, set()),
    (
        optimiser.PlanRequest,
        schemas.PlanRequestIn,
        # weather/geography are server-side singleton state (api/state.py),
        # never a client-supplied payload -- see PlanRequestIn's docstring.
        {"weather", "geography"},
    ),
    (optimiser.LegTarget, schemas.LegTargetModel, set()),
    (optimiser.Alteration, schemas.AlterationModel, set()),
    (optimiser.Candidate, schemas.CandidateModel, set()),
    (optimiser.PruneDiagnostic, schemas.PruneDiagnosticModel, set()),
    (weights.Weights, schemas.WeightsModel, set()),
    (optimiser.PlanResult, schemas.PlanResultOut, set()),
]


def _core_field_names(dc: type) -> set[str]:
    return {f.name for f in dataclasses.fields(dc)}


def _model_field_names(model: type) -> set[str]:
    return set(model.model_fields.keys())


def test_all_pairs_are_dataclasses_and_models():
    for core_dc, model, _ in PAIRS:
        assert dataclasses.is_dataclass(core_dc), f"{core_dc} is not a dataclass"
        assert hasattr(model, "model_fields"), f"{model} is not a pydantic model"


def test_no_undeclared_field_drift():
    for core_dc, model, allowed_core_only in PAIRS:
        core_fields = _core_field_names(core_dc)
        model_fields = _model_field_names(model)

        core_only = core_fields - model_fields
        model_only = model_fields - core_fields

        unexplained_core_only = core_only - allowed_core_only
        assert not unexplained_core_only, (
            f"{core_dc.__name__} has fields not mirrored in {model.__name__} "
            f"and not in its allow-list: {unexplained_core_only}"
        )
        # A pydantic-only field is never intentional for a straight mirror
        # (PlanRequestIn's extra `vessel` field is a same-name match, not
        # pydantic-only) -- any surviving model_only field is drift.
        assert not model_only, (
            f"{model.__name__} has fields not present in {core_dc.__name__}: "
            f"{model_only} -- either core/ dropped a field or the mirror "
            f"added one that needs an explicit allow-list entry"
        )
        # allow-list entries that no longer describe a real gap are stale
        # and should be removed, not left to silently mask a future typo.
        stale_allowed = allowed_core_only - core_only
        assert not stale_allowed, (
            f"{core_dc.__name__}'s allow-list claims {stale_allowed} is a "
            f"deliberate omission, but it's actually present in "
            f"{model.__name__} now -- remove it from the allow-list"
        )
