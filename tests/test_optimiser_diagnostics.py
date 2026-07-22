"""Ticket N1: end-to-end `optimise()` coverage for the `weather_data_gap`
`PruneDiagnostic` -- fires when `PruneStats.non_finite_cost_count > 0` and
either the candidate pool is empty or `missed_window` is True (the second
leg a required review addition: a data gap can prune away just the fast
route while a slower, still-feasible one survives, showing up as a window
miss rather than an empty pool -- `docs/plans/ticket-N1.md`).

A genuine implementation finding, not glossed over: isolating "empty pool
*without* `missed_window`" turned out not to be reliably constructible
through `optimise()`'s real, pre-existing design. `_baseline_route`
shares its own `baseline_reachable` with the candidate search's
`reachable` whenever no window is set (both derived from the same
`arrival_times_within` pass) -- so whatever poisons the candidate search
into finding nothing also poisons baseline into finding nothing, and
baseline's own explicit `RuntimeError` fires before `optimise()` ever
returns a `PlanResult` to inspect. Baseline's `baseline_reachable` only
diverges from `reachable` (a *generous* `DEFAULT_HORIZON_H`, independent
of a tight request window) when `latest_arrival_h` is set tighter than
that default -- which is exactly the `missed_window` case. The empty-pool
test below is therefore combined with `missed_window=True` deliberately,
not as a shortcut -- it's the one way this scenario is genuinely
reachable, and it still faithfully exercises the `not pool` leg of the
trigger (the `or` is satisfied either way).
"""

from __future__ import annotations

import dataclasses

from core.corridors import PORTS
from core.geography import SyntheticGeography
from core.optimiser import PlanRequest, build_lattice, optimise
from core.units import LatLon, distance_m, m_to_nm
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


class _PositionalNanWeatherField:
    """calm() everywhere except within `tolerance_nm` of `poison_point`,
    where `hs_m` is NaN -- see `tests/test_optimiser_constraints.py`'s
    twin of this class for the full rationale."""

    def __init__(self, poison_point: LatLon, tolerance_nm: float, ref_lat_deg: float):
        self._inner = SyntheticWeatherField("calm")
        self._poison = poison_point
        self._tol_nm = tolerance_nm
        self._ref_lat_deg = ref_lat_deg

    def sample(self, lat_deg, lon_deg, t_h):
        s = self._inner.sample(lat_deg, lon_deg, t_h)
        d_nm = m_to_nm(
            distance_m(LatLon(lat_deg, lon_deg), self._poison, self._ref_lat_deg)
        )
        if d_nm <= self._tol_nm:
            return dataclasses.replace(s, hs_m=float("nan"))
        return s


def test_weather_data_gap_fires_when_the_pool_ends_up_empty():
    """A wide enough poison forces every candidate route into a real
    detour (5.4h vs. 4.6h clean); a `latest_arrival_h` tighter than even
    the detoured duration makes `reachable` (filtered to that tight
    window) exclude the destination entirely -- `candidates_all` (hence
    `pool`) ends up empty. `missed_window` is also True here (see module
    docstring for why that's unavoidable, not a test weakness) -- the
    diagnostic firing is what's under test."""
    vessel = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    geo = SyntheticGeography()
    origin, destination = LatLon(43.0, 7.5), LatLon(42.3, 8.3)
    lattice = build_lattice(origin, destination)
    poison_point = lattice.point(lattice.n_stages // 2, 0)
    wx = _PositionalNanWeatherField(poison_point, tolerance_nm=15.0, ref_lat_deg=42.65)

    result = optimise(
        PlanRequest(
            weather=wx,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            origin=origin,
            destination=destination,
            speeds_kn=(12.0,),
            latest_arrival_h=5.0,
        )
    )

    assert result.candidates == ()
    assert result.missed_window is True
    codes = [d.code for d in result.diagnostics]
    assert "weather_data_gap" in codes


def test_weather_data_gap_fires_on_a_missed_window_with_a_nonempty_pool():
    """The review-added trigger leg, isolated: a harmless prune (real
    alternative routes exist, `optimise()` still returns candidates) is
    combined with an independently tight `latest_arrival_h` that no
    candidate meets -- `pool` is non-empty (this is exactly the case an
    empty-pool-only trigger would have missed)."""
    vessel = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    geo = SyntheticGeography()
    lattice = build_lattice(PORTS["antibes"], PORTS["portocervo"])
    poison_point = lattice.point(lattice.n_stages // 2, 0)
    wx = _PositionalNanWeatherField(poison_point, tolerance_nm=4.0, ref_lat_deg=42.3)

    result = optimise(
        PlanRequest(
            weather=wx, geography=geo, vessel=vessel, pace=50, comfort=50, latest_arrival_h=0.5
        )
    )

    assert len(result.candidates) > 0
    assert result.missed_window is True
    codes = [d.code for d in result.diagnostics]
    assert "eta_window_infeasible" in codes  # the pre-existing mechanism, unaffected
    assert "weather_data_gap" in codes


def test_weather_data_gap_does_not_fire_on_a_harmless_prune():
    """Anti-noise: the same poison as the missed-window test above, but
    with no window pressure at all -- candidates are found, and the
    diagnostic must *not* fire (most `non_finite_cost` prunes have an
    alternative route and never need surfacing, `docs/plans/ticket-N1.md`
    §3)."""
    vessel = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    geo = SyntheticGeography()
    lattice = build_lattice(PORTS["antibes"], PORTS["portocervo"])
    poison_point = lattice.point(lattice.n_stages // 2, 0)
    wx = _PositionalNanWeatherField(poison_point, tolerance_nm=4.0, ref_lat_deg=42.3)

    result = optimise(
        PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=50, comfort=50)
    )

    assert len(result.candidates) > 0
    assert result.missed_window is False
    codes = [d.code for d in result.diagnostics]
    assert "weather_data_gap" not in codes
