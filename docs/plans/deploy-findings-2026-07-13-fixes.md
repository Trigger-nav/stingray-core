# Fixes for the three Hetzner deploy findings (2026-07-13)

## Context

`docs/plans/deploy-findings-2026-07-13.md` records three defects found
during the first real cloud deploy (Hetzner CPX21). The deploy itself
succeeded end-to-end (real TLS, real cron, real demo traffic) — these are
hardening fixes before relying on cron unattended, in the priority order
that findings doc gives. This plan covers all three plus the two
documentation follow-ups it calls for (tick the now-verified manual
checklist items, reflect the reorder in the runbook itself).

**No changes to `core/`.** All three fixes are in `deploy/README.md`,
`deploy/linux/install.sh`, `api/state.py`, and `ingest/`.

## Fix 1 — runbook ordering bug (fresh-box crash-loop)

**Root cause:** `deploy/README.md`'s Cloud VM runbook runs step 4 (install
+ start `stingray-planner.service`) before step 6 (ingest venv + first real
weather fetch). `data/weather/*.npz` is gitignored, so a fresh clone has no
weather file; `install.sh`'s `data/` copy (already fixed pre-Hetzner, see
CLAUDE.md's install.sh gotcha) copies nothing into `.../data/weather/`
either, since there's nothing there to copy. `api/state.py`'s
`AppState.__init__` calls `GriddedWeatherField.from_npz(...)`
synchronously during FastAPI's lifespan startup, which raises a bare
`FileNotFoundError` — uvicorn logs an unhandled-exception traceback and
exits; systemd restarts it; same failure — crash-loop (`status=3`) until
someone runs a fetch by hand.

**Fix — reorder + clearer failure message, both (per the findings doc,
"do at least the first two"):**

### 1a. Reorder the runbook

Move the ingest-venv-setup-and-first-fetch work from step 6 to a new step
4, **before** `install.sh` runs, and point that first fetch at the
checkout's own default `data/weather/` path (not `/opt/stingray/...`) —
so it's already sitting in `data/` when `install.sh`'s existing `cp -r
data/. /opt/stingray/data/` step runs, and the service has a real weather
file the first time it ever starts. The recurring cron schedule (which
*does* target `/opt/stingray/data/weather/...` directly, and needs
`/opt/stingray` to exist first) stays a later step, after install.

New order: 1 base packages, 2 get the code, 3 build the binary, **4
ingest venv + first real fetch (into the checkout's `data/`)**, 5 install
(`install.sh`), 6 configure `.env`, 7 install the recurring cron
schedule, 8 TLS (Caddy), 9 firewall, 10 point the demo at this instance,
11 end-to-end smoke test. Steps 5–11 are today's steps 4–5, 7–10
renumbered, with the crontab-install paragraph split out of old step 6
into its own step 7.

### 1b. Explicit startup failure message

`api/state.py` currently calls `GriddedWeatherField.from_npz(config.weather_npz_path)`
directly in three places (`AppState.__init__`, `_worker_init`, the
hot-swap reload path) — each lets a bare `FileNotFoundError` propagate.
Add one small helper next to the existing `_geography_kwargs`:

```python
def _load_weather(path: str) -> GriddedWeatherField:
    try:
        return GriddedWeatherField.from_npz(path)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"no weather file at {path!r} -- run ingest.fetch_grib_nomads or "
            "ingest.fetch_grib_ecmwf first (deploy/README.md's Cloud VM "
            "runbook, step 4)"
        ) from exc
```

Use it at all three existing call sites. This doesn't prevent the
crash-loop by itself (systemd will still restart-and-fail if the file is
genuinely still missing) — it makes `journalctl -u stingray-planner`
immediately actionable instead of a bare traceback, which is what step
5's troubleshooting note in the runbook already half-promises
("`FileNotFoundError` there means step 4's `data/` copy didn't happen") —
this makes that promise literally true in the log output.

### 1c. `install.sh` warns on an empty weather dir (cheap extra, same failure mode via a different path)

After the existing `cp -r data/. INSTALL_DIR/data/` line, a one-line check
before starting the service:

```bash
if ! find "${INSTALL_DIR}/data/weather" -name '*.npz' -print -quit | grep -q .; then
  echo "WARNING: no *.npz found in ${INSTALL_DIR}/data/weather -- the" >&2
  echo "service will crash-loop until a weather fetch runs (see" >&2
  echo "deploy/README.md's Cloud VM runbook, step 4)." >&2
fi
```

Covers the case where someone runs `install.sh` out of order (or step 4's
fetch silently failed) despite the reordered runbook — install still
proceeds (matching the script's existing non-interactive, no-prompts
style) but now says why the next `systemctl status` will be unhappy.

## Fix 2 — per-source publication delay and valid-cycle set

**Root cause:** `ingest/grib_common.py`'s `latest_available_cycle_utc`
hard-codes a single 6-hourly cycle grid (`(available.hour // 6) * 6`) and
a single `delay_h` default (5.0), tuned for NOMADS. ECMWF open data (1)
publishes ~8–9h after cycle time, not ~5h, and (2) the `oper`/`wave`
streams only ever have **00z/12z** cycles — the shared 6-hourly rounding
can select a 06z/18z that never exists for ECMWF at all. Live symptom:
a 17:16 UTC run picked today's not-yet-published 12z and died on `HTTP
404` fetching the `.index` sidecar.

**Fix:** generalise `latest_available_cycle_utc` to take an explicit
`valid_hours` set (keyword-only, default `(0, 6, 12, 18)` — preserves
today's behaviour and every existing test unchanged), and pick the latest
member of that set at or before `(now_utc - delay_h)`, with day-rollback
when the delay pushes before the day's first valid hour:

```python
def latest_available_cycle_utc(
    now_utc: datetime,
    *,
    delay_h: float = 5.0,
    valid_hours: tuple[int, ...] = (0, 6, 12, 18),
) -> tuple[str, str]:
    available = now_utc - timedelta(hours=delay_h)
    hours = sorted(valid_hours)
    eligible = [h for h in hours if h <= available.hour]
    if eligible:
        cycle_dt = available.replace(hour=eligible[-1], minute=0, second=0, microsecond=0)
    else:
        cycle_dt = (available - timedelta(days=1)).replace(
            hour=hours[-1], minute=0, second=0, microsecond=0
        )
    return cycle_dt.strftime("%Y%m%d"), f"{cycle_dt.hour:02d}"
```

Each fetcher gets its own explicit, cited constants and passes them
through (self-documenting, matches this repo's existing citation style
rather than leaning on a shared default that happens to be right for one
source):

- `fetch_grib_nomads.py`: `NOMADS_DELAY_H = 5.0`, `NOMADS_VALID_HOURS =
  (0, 6, 12, 18)` — unchanged behaviour, now explicit instead of
  implicit-via-default.
- `fetch_grib_ecmwf.py`: `ECMWF_DELAY_H = 9.0`, `ECMWF_VALID_HOURS = (0,
  12)` — per the findings doc ("ECMWF: {00,12}, delay ≥9h").

## Fix 3 — 404-cycle fallback so cron self-heals

**Root cause:** today, a 404 on the selected cycle raises straight out of
`main()`. Under cron this just means a failed run and stale weather with
nothing flagging it in the served payload — `/v1/health`'s provenance
*shows* the cycle so staleness is visible if someone looks, but nothing
makes the fetcher recover on its own.

**Fix:** a small, pure, independently-testable retry helper in
`ingest/grib_common.py` — deliberately *not* baked into `build_grid`
itself, so it's testable with a fake `attempt` callable and no real
network/cfgrib dependency:

```python
def previous_cycle_utc(
    cycle_date: str, cycle_hour: str, *, valid_hours: tuple[int, ...] = (0, 6, 12, 18)
) -> tuple[str, str]:
    """The cycle immediately before (cycle_date, cycle_hour) in
    `valid_hours`'s cadence -- used by `fetch_with_cycle_fallback` to step
    back one cycle at a time on a 404 (finding #3, 2026-07-13 Hetzner
    deploy: a 404 on the selected cycle should self-heal, not die)."""
    dt = datetime.strptime(f"{cycle_date}{cycle_hour}", "%Y%m%d%H")
    hours = sorted(valid_hours)
    idx = hours.index(dt.hour)
    if idx == 0:
        prev_dt = (dt - timedelta(days=1)).replace(hour=hours[-1])
    else:
        prev_dt = dt.replace(hour=hours[idx - 1])
    return prev_dt.strftime("%Y%m%d"), f"{prev_dt.hour:02d}"


def fetch_with_cycle_fallback(
    cycle_date: str,
    cycle_hour: str,
    *,
    valid_hours: tuple[int, ...],
    max_attempts: int,
    attempt,  # Callable[[str, str], T]
):
    """Try `attempt(cycle_date, cycle_hour)`; on an HTTP 404 (cycle not
    yet published -- the observed live failure mode), step back to the
    previous valid cycle and retry, up to `max_attempts` cycles total, so
    a cron job self-heals past a publication-timing race instead of
    dying. Any other exception (a real network/parse failure, not "this
    cycle doesn't exist yet") propagates immediately -- silently retrying
    past a genuine error would mask it, not fix it. Returns
    `(date, hour, attempt(date, hour))` for whichever cycle succeeded."""
    date, hour = cycle_date, cycle_hour
    for attempt_no in range(max_attempts):
        try:
            return date, hour, attempt(date, hour)
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or attempt_no == max_attempts - 1:
                raise
            date, hour = previous_cycle_utc(date, hour, valid_hours=valid_hours)
    raise AssertionError("unreachable")  # loop always returns or re-raises
```

`max_attempts=3` in both fetchers: for ECMWF's 12h-apart cycles that
covers a full 24h back (current, -12h, -24h); for NOMADS' 6h-apart
cycles, 12h back — comfortably past any realistic publication-timing
race, without letting a genuinely broken endpoint retry forever under
cron.

**Wiring into `main()`, both fetchers** — fallback only applies to the
**auto-selected** cycle (cron's actual usage), not an explicit
`--cycle-date --cycle-hour` (a human asking for a specific cycle should
get exactly that cycle, or a clear error, not a silent substitution):

```python
if args.cycle_date and args.cycle_hour:
    cycle_date, cycle_hour = args.cycle_date, args.cycle_hour
    grid = build_grid(cycle_date, cycle_hour, args.horizon_h, geography)
else:
    cycle_date, cycle_hour = latest_available_cycle_utc(now_utc, delay_h=..., valid_hours=...)
    cycle_date, cycle_hour, grid = fetch_with_cycle_fallback(
        cycle_date, cycle_hour,
        valid_hours=...,
        max_attempts=3,
        attempt=lambda d, h: build_grid(d, h, args.horizon_h, geography),
    )
```

**Caveat, stated not hidden:** the live 404 was observed on ECMWF's
`.index` sidecar fetch specifically. NOMADS' grib-filter CGI's actual
behaviour when a cycle isn't published yet hasn't been observed live
(only ECMWF's has, this deploy) — applying the same fallback to
`fetch_grib_nomads.py` is defensive-by-construction (same shape of
problem, cron races a publish window either way) but its 404-ness is
unverified until it's actually hit in the wild.

## Tests

`tests/test_grib_common.py` (mocked, no network/cfgrib — matches this
file's existing pure-function style):

- `latest_available_cycle_utc` with `valid_hours=(0, 12)`: a time whose
  `now_utc - delay_h` lands at an hour that would round to 06z/18z under
  the old fixed-6-hourly logic must instead select the nearest valid hour
  at or before it from `{0, 12}` — e.g. 17:16 UTC, `delay_h=9.0` →
  08:16 available → selects `00z`, not `12z` (the exact live-observed
  case) and never `06z`/`18z` regardless of input time.
- `latest_available_cycle_utc`'s existing NOMADS-shaped tests
  (`valid_hours` left at its default) must keep passing unchanged —
  regression guard that the generalisation didn't change default
  behaviour.
- `previous_cycle_utc`: mid-list step-back (`(date, "12")` →
  `(date, "00")` for `(0, 12)`); wrap-to-previous-day at the first valid
  hour (`(date, "00")` → `(date-1, "12")` for `(0, 12)`, and
  `(date, "00")` → `(date-1, "18")` for the default 4-cycle set).
- `fetch_with_cycle_fallback`, the mocked regression test the findings
  doc asks for: a fake `attempt` that raises `HTTPError(..., code=404,
  ...)` for the first N-1 cycles then returns a sentinel value for the
  Nth — asserts the sentinel is returned, the correct final `(date,
  hour)` is returned, and `attempt` was called exactly N times with the
  expected cycles in descending order. A second case: a non-404
  `HTTPError` (or any other exception) propagates on the first call
  without any retry. A third case: 404 on every attempt up to
  `max_attempts` re-raises the last `HTTPError` rather than swallowing
  it.

No new test needed for fix 1b beyond a direct unit test of `_load_weather`
raising `RuntimeError` (not `FileNotFoundError`) with the path and a
"run ingest..." hint in the message, given a nonexistent path —
add to `tests/test_api_*` alongside the existing `api/state.py` coverage
(or a new small `tests/test_api_state.py` if none exists yet for this
module — check before adding a new file).

## Docs

- `deploy/README.md`: apply the reorder (fix 1a) to the numbered Cloud VM
  runbook sections; tick the now-verified manual checklist items per this
  deploy (Linux PyInstaller build on the target box; `install.sh` incl.
  the `data/` copy fix; Ubuntu 24.04 `libeccodes-dev` route; real Let's
  Encrypt issuance; full end-to-end plan over public TLS) — leave the
  still-open items (Windows/macOS CI-runner builds, real gateway/hardware
  smoke test, real weather-sync smoke test) unchecked.
- `CLAUDE.md`: after implementation, a new gotcha entry recording the
  three findings and fixes (mirroring the existing GRIB-conventions/
  install.sh-data-gap gotchas' style) — the source-specific
  delay/valid-hours split and the cycle-fallback mechanism are exactly
  the kind of empirically-found, non-obvious-from-the-code fact that
  section exists for.

## Verification

- `pytest -m ""` green, `ruff check .` clean.
- No live network calls in the new tests — `fetch_with_cycle_fallback`'s
  `attempt` is injected, so this is enforceable by construction, not by
  discipline.
