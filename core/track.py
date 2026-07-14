"""Historical-track data contract (ticket B7 step 0). `TrackPoint` is the
one `(t, lat, lon, ...)` row shape both `ingest/`'s bbox-from-track and
ERA5-annotator tooling (Part 1/2) and `fit/`'s import layer (Part 3) agree
on -- living in `core/` (not `ingest/` or `fit/`) for the same reason
`core.weather.WeatherSample` does: it's the one package both `ingest/` and
`fit/` already depend on one-way, and `ingest/`/`fit/` never depend on
each other (CLAUDE.md's package-layout convention). Pure computation, zero
I/O, numpy-free -- consistent with `core/`'s zero-I/O-side-effects
boundary. Not routing/lattice code; does not touch anything under the
optimiser feature freeze.

`ingest/`'s Part 2 (ERA5 annotator) and `fit/`'s Part 3 (import adapters)
never import each other -- their shared shape is a *file-format* contract
(a CSV using `TRACK_CSV_COLUMNS` below), not a code dependency. A small,
deliberate duplication of CSV-reading logic in both packages is the
trade-off for preserving the one-way layering invariant.
"""

from __future__ import annotations

from dataclasses import dataclass

# Shared CSV column order/names -- ingest/track_io.py's writer and fit/'s
# AnnotatedTrackCsvAdapter reader both key off these constants so the two
# packages agree on the file format without importing each other.
TRACK_CSV_COLUMNS = (
    "t_epoch_s",
    "lat_deg",
    "lon_deg",
    "hs_m",
    "period_peak_s",
    "period_mean_s",
    "wave_from_deg",
    "wind_u_ms",
    "wind_v_ms",
)


@dataclass(frozen=True)
class TrackPoint:
    """One historical-track sample. `t_epoch_s`/`lat_deg`/`lon_deg` are
    always known (that's what makes it a track); the environmental fields
    start `None` and are filled in by `ingest/fetch_era5_track.py`'s
    annotator (Part 2) -- `period_mean_s` mirrors
    `core.weather.WeatherSample`'s `period_peak_s`/`period_mean_s` pair
    (additive, not load-bearing for any fitting physics; `period_peak_s`
    is the field the added-resistance component actually uses)."""

    t_epoch_s: float
    lat_deg: float
    lon_deg: float
    hs_m: float | None = None
    period_peak_s: float | None = None
    period_mean_s: float | None = None
    wave_from_deg: float | None = None
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None


def covering_bbox(
    points: list[TrackPoint], margin_deg: float = 0.25
) -> tuple[float, float, float, float]:
    """Smallest `(lon_min, lat_min, lon_max, lat_max)` bbox (matching
    `core.geography.OPERATING_AREA_BBOX`'s tuple shape/order) containing
    every point plus a flat degree margin on each side.
    `margin_deg=0.25` (~15nm at mid-latitudes) is a judgment call, not
    derived -- enough slack for a passage's lattice/interpolation needs
    without ballooning a long track's bbox unnecessarily.

    Raises `ValueError` if the track's longitude spread exceeds 180deg --
    the cheap, reliable signal of an antimeridian (+/-180deg) crossing
    rather than a genuinely huge bbox (no real vessel passage should span
    that wide under this simple lon_min/lon_max convention). Known
    limitation, not fixed here: proper antimeridian handling needs
    unwrapped/circular longitude arithmetic throughout the bbox/ingest
    chain -- irrelevant for the Med/UK operating area this ticket targets,
    and ROADMAP.md's R3 ("Ocean passages") already scopes real spherical
    geometry as its own future ticket."""
    if not points:
        raise ValueError("covering_bbox requires at least one TrackPoint")
    lats = [p.lat_deg for p in points]
    lons = [p.lon_deg for p in points]
    lon_min, lon_max = min(lons), max(lons)
    if lon_max - lon_min > 180.0:
        raise ValueError(
            f"track longitude spread ({lon_max - lon_min:.1f} deg, "
            f"{lon_min} to {lon_max}) exceeds 180 deg -- this looks like an "
            "antimeridian-crossing track, which covering_bbox does not "
            "support (see this function's docstring)"
        )
    lat_min, lat_max = min(lats), max(lats)
    return (
        lon_min - margin_deg,
        lat_min - margin_deg,
        lon_max + margin_deg,
        lat_max + margin_deg,
    )


def covering_time_range_s(points: list[TrackPoint]) -> tuple[float, float]:
    """`(min, max)` epoch-seconds spanned by the track."""
    if not points:
        raise ValueError("covering_time_range_s requires at least one TrackPoint")
    times = [p.t_epoch_s for p in points]
    return min(times), max(times)
