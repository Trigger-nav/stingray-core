"""Import-row -> fit-ready-segment orchestration (ticket B7 Part 3,
**amendment 1** from plan review).

The original draft of this ticket converted `CanonicalImportRow` ->
`TelemetrySample` (which has no vessel/passage/source concept at all, by
design) and ran the unchanged `extract_steady_state_segments` on top --
which silently produced `vessel_id=None, passage_id=None,
fuel_noise_multiplier=1.0` on **every** resulting segment, regardless of
what source or vessel the data actually came from. Two real consequences
review caught: (a) `fit/validate.py`'s `passage_holdout_split` can never
group high-frequency imported data (every id is `None`); (b) `fit/
import_schema.py`'s `SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION` never reaches
the residual weighting on the high-frequency path.

Fix, this module: orchestration runs per `(vessel_id, passage_id, source)`
batch -- normally one batch per `ImportAdapter.parse()` call, since every
row an adapter produces shares the `vessel_id`/`passage_id` it was called
with and the same `source` string. `extract_steady_state_segments`
(`fit/segments.py`) itself stays completely untouched -- it has no
provenance concept and shouldn't grow one; provenance/noise are stamped
onto its *output* afterward via `dataclasses.replace`, in
`stamp_segment_provenance`. The low-frequency (`daily_rows_to_segments`)
path never loses identity in the first place, since it never round-trips
through the identity-stripping `TelemetrySample` shape -- it uses the same
per-source multiplier lookup for consistency, not a disconnected hardcoded
value.
"""

from __future__ import annotations

import dataclasses

from fit.import_schema import (
    SOG_FALLBACK_NOISE_MULTIPLIER,
    SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION,
    CanonicalImportRow,
)
from fit.segments import (
    DEFAULT_FUEL_JUMP_TOL_FRACTION,
    DEFAULT_HEADING_TOL_DEG,
    DEFAULT_MAX_GAP_S,
    DEFAULT_MIN_DURATION_S,
    DEFAULT_SPEED_TOL_KN,
    SteadyStateSegment,
    extract_steady_state_segments,
)
from fit.telemetry import TelemetrySample

# fit.calm_resistance/fit.added_resistance both default
# fuel_noise_std_fraction to this same value -- the reference point
# _source_fuel_noise_multiplier expresses every per-source fraction
# relative to. Duplicated as a literal (not imported from
# fit.calm_resistance) to avoid a fit.import_pipeline -> fit.
# calm_resistance dependency for one constant; the two are kept in sync
# by fit/priors.py-style convention, not machinery -- pre-existing
# duplication (fit.calm_resistance/fit.added_resistance already both
# define this independently), not new to this ticket.
DEFAULT_FUEL_NOISE_STD_FRACTION = 0.03

DEFAULT_LOW_FREQ_NOISE_MULTIPLIER = 5.0

KN_TO_MS = 0.5144444444444445


def _source_fuel_noise_multiplier(source: str, *, uses_sog_fallback: bool = False) -> float:
    """`SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION[source]` expressed relative
    to fit's own global default (`DEFAULT_FUEL_NOISE_STD_FRACTION=0.03`)
    -- what `SteadyStateSegment.fuel_noise_multiplier` actually multiplies
    against in `fit_calm_resistance`/`fit_added_resistance`'s residual
    weighting. Always relative to the *default* fraction, not whatever a
    caller happens to pass `fit_twin` at call time -- a documented
    simplification, not a bug. `SOG_FALLBACK_NOISE_MULTIPLIER` stacks on
    top when the batch's speed channel is SOG standing in for STW (ticket
    B7 Part 3's STW-vs-SOG handling)."""
    fraction = SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION.get(source, DEFAULT_FUEL_NOISE_STD_FRACTION)
    multiplier = fraction / DEFAULT_FUEL_NOISE_STD_FRACTION
    return multiplier * (SOG_FALLBACK_NOISE_MULTIPLIER if uses_sog_fallback else 1.0)


def stamp_segment_provenance(
    segments: list[SteadyStateSegment],
    *,
    vessel_id: str,
    passage_id: str,
    source: str,
    uses_sog_fallback: bool = False,
) -> list[SteadyStateSegment]:
    """Post-extraction identity/noise stamping via `dataclasses.replace`
    -- `extract_steady_state_segments` itself stays completely untouched."""
    multiplier = _source_fuel_noise_multiplier(source, uses_sog_fallback=uses_sog_fallback)
    return [
        dataclasses.replace(
            seg, vessel_id=vessel_id, passage_id=passage_id, fuel_noise_multiplier=multiplier
        )
        for seg in segments
    ]


def canonical_rows_to_telemetry_samples(rows: list[CanonicalImportRow]) -> list[TelemetrySample]:
    """Converts high-frequency `CanonicalImportRow`s into `fit/
    telemetry.py`'s existing `TelemetrySample` shape so `extract_steady_
    state_segments`/`fit_twin` run completely unmodified downstream.
    **Deliberately drops identity** (`vessel_id`/`passage_id`/`source`) --
    `TelemetrySample` has no such concept by design, and `rows_to_segments`
    (below) is what restores it afterward via `stamp_segment_provenance`;
    don't read this function in isolation and assume it's the whole
    pipeline.

    Every row must carry a speed (`stw_kn` preferred, `sog_kn` as a
    fallback -- ticket B7 Part 3's STW-vs-SOG handling) and every other
    field `TelemetrySample` needs (`heading_deg`/`active_engines`/
    `fuel_kg_per_h`/`hs_m`/`period_peak_s`/`wave_from_deg`); raises
    `ValueError` naming the first offending row rather than silently
    inventing a placeholder (CLAUDE.md's "no invented numbers"). A source
    with no logged sea state (a plain e-logbook/noon-report export) needs
    its positions annotated first (`ingest/fetch_era5_track.py`, Part 2)
    before it can reach this function -- row-joining across sources by
    `passage_id` is not solved by this ticket."""
    if not rows:
        return []
    t0 = rows[0].t_epoch_s
    samples = []
    for r in rows:
        speed_kn = r.stw_kn if r.stw_kn is not None else r.sog_kn
        missing = [
            name
            for name, value in (
                ("stw_kn/sog_kn", speed_kn),
                ("heading_deg", r.heading_deg),
                ("active_engines", r.active_engines),
                ("fuel_kg_per_h", r.fuel_kg_per_h),
                ("hs_m", r.hs_m),
                ("period_peak_s", r.period_peak_s),
                ("wave_from_deg", r.wave_from_deg),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"row at t_epoch_s={r.t_epoch_s} (source={r.source!r}) is missing "
                f"{missing} -- needs ERA5 annotation (ingest.fetch_era5_track) or a "
                "richer source before it can feed the high-frequency fitting path"
            )
        samples.append(
            TelemetrySample(
                t_h=(r.t_epoch_s - t0) / 3600.0,
                stw_ms=speed_kn * KN_TO_MS,
                heading_deg=r.heading_deg,
                active_engines=r.active_engines,
                fuel_kg_per_h=r.fuel_kg_per_h,
                hs_m=r.hs_m,
                period_peak_s=r.period_peak_s,
                wave_from_deg=r.wave_from_deg,
                wind_u_ms=r.wind_u_ms,
                wind_v_ms=r.wind_v_ms,
            )
        )
    return samples


def daily_rows_to_segments(
    rows: list[CanonicalImportRow],
    *,
    low_freq_noise_multiplier: float = DEFAULT_LOW_FREQ_NOISE_MULTIPLIER,
) -> list[SteadyStateSegment]:
    """Low-frequency (daily-aggregate) entry path -- constructs one
    `SteadyStateSegment` per row directly, **bypassing `extract_steady_
    state_segments` entirely** (per the ROADMAP's own wording). Never
    loses identity in the first place (each row already carries its own
    `vessel_id`/`passage_id`/`source`) -- uses the same per-source
    `_source_fuel_noise_multiplier` lookup the high-frequency path uses,
    with `low_freq_noise_multiplier` stacked on top, not a disconnected
    hardcoded value. Requires `hs_m`/`period_peak_s`/`wave_from_deg`
    (same reasoning as `canonical_rows_to_telemetry_samples` -- a plain
    noon report has none of these by default, per `NoonReportAdapter`'s
    "usually no wave/motion data" framing, and needs ERA5 annotation
    first) **and `heading_deg`/`active_engines`** (review fix: the
    original draft silently defaulted these to `0.0`/`1` when missing --
    `fit/added_resistance.py` consumes `mean_heading_deg` for the
    relative wave angle and `active_engines` is central to the calm/SFOC
    fit's identifiability (ticket 0.6's finding #1); fabricating either
    would silently corrupt both fits, exactly what "no invented numbers"
    forbids)."""
    segments = []
    for r in rows:
        speed_kn = r.stw_kn if r.stw_kn is not None else r.sog_kn
        missing = [
            name
            for name, value in (
                ("stw_kn/sog_kn", speed_kn),
                ("heading_deg", r.heading_deg),
                ("active_engines", r.active_engines),
                ("fuel_kg_per_h", r.fuel_kg_per_h),
                ("hs_m", r.hs_m),
                ("period_peak_s", r.period_peak_s),
                ("wave_from_deg", r.wave_from_deg),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"daily row at t_epoch_s={r.t_epoch_s} (source={r.source!r}) is missing "
                f"{missing} -- needs ERA5 annotation (ingest.fetch_era5_track) first"
            )
        uses_sog_fallback = r.stw_kn is None
        multiplier = (
            _source_fuel_noise_multiplier(r.source, uses_sog_fallback=uses_sog_fallback)
            * low_freq_noise_multiplier
        )
        t_start_h = r.t_epoch_s / 3600.0
        segments.append(
            SteadyStateSegment(
                t_start_h=t_start_h,
                t_end_h=t_start_h + 24.0,  # one daily-aggregate row -- a placeholder span,
                mean_stw_ms=speed_kn * KN_TO_MS,  # unused by any current fitting/validation logic
                mean_heading_deg=r.heading_deg,
                active_engines=r.active_engines,
                mean_fuel_kg_per_h=r.fuel_kg_per_h,
                mean_hs_m=r.hs_m,
                mean_period_peak_s=r.period_peak_s,
                mean_wave_from_deg=r.wave_from_deg,
                duration_h=24.0,
                n_samples=1,
                vessel_id=r.vessel_id,
                passage_id=r.passage_id,
                fuel_noise_multiplier=multiplier,
            )
        )
    return segments


def rows_to_segments(
    rows: list[CanonicalImportRow],
    *,
    low_freq_noise_multiplier: float = DEFAULT_LOW_FREQ_NOISE_MULTIPLIER,
    **extract_kwargs,
) -> list[SteadyStateSegment]:
    """Top-level orchestration -- the actual answer to "how does a batch
    of imported rows become fit-ready segments with correct provenance."
    Splits low- vs high-frequency rows; groups high-frequency rows by
    `(vessel_id, passage_id, source)` (normally one group per adapter
    `parse()` call); runs the unchanged `canonical_rows_to_telemetry_
    samples` + `extract_steady_state_segments` per group, then stamps;
    low-frequency rows go through `daily_rows_to_segments` directly
    (already source-tagged per row). `**extract_kwargs` (e.g.
    `min_duration_s`, `speed_tol_kn`) pass through to `extract_steady_
    state_segments` unchanged."""
    low_freq_rows = [r for r in rows if r.is_low_frequency]
    high_freq_rows = [r for r in rows if not r.is_low_frequency]

    segments: list[SteadyStateSegment] = list(
        daily_rows_to_segments(low_freq_rows, low_freq_noise_multiplier=low_freq_noise_multiplier)
    )

    groups: dict[tuple[str, str, str], list[CanonicalImportRow]] = {}
    for r in high_freq_rows:
        groups.setdefault((r.vessel_id, r.passage_id, r.source), []).append(r)

    for (vessel_id, passage_id, source), group_rows in groups.items():
        samples = canonical_rows_to_telemetry_samples(group_rows)
        group_segments = extract_steady_state_segments(
            samples,
            min_duration_s=extract_kwargs.get("min_duration_s", DEFAULT_MIN_DURATION_S),
            speed_tol_kn=extract_kwargs.get("speed_tol_kn", DEFAULT_SPEED_TOL_KN),
            heading_tol_deg=extract_kwargs.get("heading_tol_deg", DEFAULT_HEADING_TOL_DEG),
            fuel_jump_tol_fraction=extract_kwargs.get(
                "fuel_jump_tol_fraction", DEFAULT_FUEL_JUMP_TOL_FRACTION
            ),
            max_gap_s=extract_kwargs.get("max_gap_s", DEFAULT_MAX_GAP_S),
        )
        # any(), not all(): a mixed group (some rows have real STW, some
        # fall back to SOG) still gets the wider SOG-fallback band -- the
        # conservative choice for a batch with partial STW dropout,
        # rather than silently under-weighting the SOG-derived samples
        # that snuck a segment-level "clean" label from their STW-having
        # neighbours (review fix).
        uses_sog_fallback = any(r.stw_kn is None for r in group_rows)
        segments.extend(
            stamp_segment_provenance(
                group_segments,
                vessel_id=vessel_id,
                passage_id=passage_id,
                source=source,
                uses_sog_fallback=uses_sog_fallback,
            )
        )

    return segments
