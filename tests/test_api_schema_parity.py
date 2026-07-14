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
# deliberate, reviewed omission -- not accidental drift}, {model-only field
# names that are a deliberate, reviewed addition}). The fourth element
# defaults to set() for every pair except PlanRequest/PlanRequestIn --
# added for ticket R1's pack_id (API) <-> region_pack (core) pair, the
# first field in this table that isn't a straight 1:1 name match: the API
# takes a string key (a client-facing wire schema has no business
# embedding a whole RegionPack), core.optimiser.PlanRequest takes the
# resolved RegionPack object itself (api/convert.py does the resolution).
PAIRS = [
    (units.LatLon, schemas.LatLonModel, set(), set()),
    (vessel_spec.HullParticulars, schemas.HullParticularsModel, set(), set()),
    (vessel_spec.CalmResistanceCurve, schemas.CalmResistanceCurveModel, set(), set()),
    (
        vessel_spec.AddedResistanceCoefficients,
        schemas.AddedResistanceCoefficientsModel,
        set(),
        set(),
    ),
    (vessel_spec.EngineSpec, schemas.EngineSpecModel, set(), set()),
    (vessel_spec.ComfortCoefficients, schemas.ComfortCoefficientsModel, set(), set()),
    (vessel_spec.WearPolicy, schemas.WearPolicyModel, set(), set()),
    (vessel_spec.VesselSpec, schemas.VesselSpecModel, set(), set()),
    (
        optimiser.PlanRequest,
        schemas.PlanRequestIn,
        # weather/geography are server-side singleton state (api/state.py),
        # never a client-supplied payload -- see PlanRequestIn's docstring.
        # region_pack is resolved from pack_id (below), not mirrored 1:1.
        {"weather", "geography", "region_pack"},
        {"pack_id"},
    ),
    (optimiser.LegTarget, schemas.LegTargetModel, set(), set()),
    (optimiser.Alteration, schemas.AlterationModel, set(), set()),
    (optimiser.Candidate, schemas.CandidateModel, set(), set()),
    (optimiser.PruneDiagnostic, schemas.PruneDiagnosticModel, set(), set()),
    (weights.Weights, schemas.WeightsModel, set(), set()),
    (optimiser.PlanResult, schemas.PlanResultOut, set(), set()),
]


def _core_field_names(dc: type) -> set[str]:
    return {f.name for f in dataclasses.fields(dc)}


def _model_field_names(model: type) -> set[str]:
    return set(model.model_fields.keys())


def test_all_pairs_are_dataclasses_and_models():
    for core_dc, model, _, _ in PAIRS:
        assert dataclasses.is_dataclass(core_dc), f"{core_dc} is not a dataclass"
        assert hasattr(model, "model_fields"), f"{model} is not a pydantic model"


def test_no_undeclared_field_drift():
    for core_dc, model, allowed_core_only, allowed_model_only in PAIRS:
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
        # pydantic-only) unless explicitly allow-listed (ticket R1's
        # pack_id, resolved into core's differently-named region_pack) --
        # any other surviving model_only field is drift.
        unexplained_model_only = model_only - allowed_model_only
        assert not unexplained_model_only, (
            f"{model.__name__} has fields not present in {core_dc.__name__} "
            f"and not in its allow-list: {unexplained_model_only} -- either "
            f"core/ dropped a field or the mirror added one that needs an "
            f"explicit allow-list entry"
        )
        # allow-list entries that no longer describe a real gap are stale
        # and should be removed, not left to silently mask a future typo.
        stale_allowed_core_only = allowed_core_only - core_only
        assert not stale_allowed_core_only, (
            f"{core_dc.__name__}'s allow-list claims {stale_allowed_core_only} is a "
            f"deliberate omission, but it's actually present in "
            f"{model.__name__} now -- remove it from the allow-list"
        )
        stale_allowed_model_only = allowed_model_only - model_only
        assert not stale_allowed_model_only, (
            f"{model.__name__}'s allow-list claims {stale_allowed_model_only} is a "
            f"deliberate addition, but it's actually present in "
            f"{core_dc.__name__} now -- remove it from the allow-list"
        )
