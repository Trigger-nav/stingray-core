# Ticket B2 — Connect the demo UI to the API

## Context

Ticket B1 (done, committed, commit `551cfe7`) built a job-shaped FastAPI
planner service (`api/`) wrapping `core.optimiser.optimise()` — submit a
plan request, get a job id, poll for the result. It has no consumer yet:
the hosted demo (`prototype/stingray_planner.html`, GitHub Pages) still
runs its original, fully synthetic, fully client-side, fully synchronous
"brain" — the `<script id="core">` block. ROADMAP.md's B2 row: "Connect
the demo UI to the API: swap the `<script id="core">` block for API
calls | 1–2 weeks; keeps the validated UX, replaces the synthetic brain —
the 'live planner' for pilot/investor demos." This is explicitly
surgical, not a rewrite: one static HTML file, no build step, no
bundler/framework (the real production bridge app is separate, later
work, ticket 2.2) — swap the brain, keep everything else recognisable.

The prototype was researched in depth this session (two rounds, grep-
verified against the actual file, not assumed): a single 868-line file,
all global scope, one call site that matters (`render()`, line 566, doing
`const res=optimise(scId,econ,comf,WEAR_POLICY,ab?+ab:null)` fully
synchronously), with real, material gaps between what the old synthetic
brain exposed and what the new job-shaped API returns — most importantly,
the vessel-state "optimality" panel depends on the *full* internal
candidate sweep (`res.cands`, ~16 candidates) and live client-side calls
into the old synthetic twin model, neither of which the new API provides
(it returns only `candidates`, the diversity-filtered top 2-3, plus
`baseline` — by design, contract point 1's job-shaped surface was never
meant to expose the whole internal search). Five scope decisions were
made with the user before finalising this plan (see Design 4-6 below for
where they land): ship a throwaway/scoped demo credential directly in the
static JS; drop the synthetic weather-scenario dropdown entirely in
favour of a real weather-provenance line from `GET /v1/health`; code
only, no live cloud VM provisioning as part of this ticket; simplify the
vessel-state optimality comparisons to `candidates`+`baseline` rather
than touching `core/optimiser.py`'s return shape (which would brush the
bridge section's optimiser feature freeze ahead of ticket 1.5); and drop
the two `vstateRows` tiles that have no server equivalent rather than
keep a synthetic twin model running client-side next to real numbers.

**Amendment (approved before implementation):** the original research pass
missed a third `SCENARIOS` call site — `drawWx()`, the chart's weather
heatmap/wind-vector layer, driven by the `#tscrub` forecast-scrub slider.
Design 5 below is extended with a new, small, read-only
`GET /v1/weather/field?h=N` endpoint (a downsampled grid for one valid
time, ETag-cached) for `drawWx()` to consume instead. Three more changes
approved in the same amendment, folded into their natural sections below:
a queue-depth cap (`429` + `Retry-After`) on `POST /v1/plans` (Design 10);
`tests/test_api_cors.py` extended to cover the new weather-field endpoint
(Design 7); and a mixed-content note for `prototype/deploy/HOSTING.md`
(Docs) — the deployed demo is served over HTTPS (GitHub Pages), so
`API_BASE` must point at an HTTPS cloud URL or the browser silently blocks
every request.

## Design

### 1. The async `optimise()` replacement

The existing `<script id="core">` block (~lines 198-413) is renamed
`id="shared"` (nothing in the file depends on the literal id "core",
confirmed by grep) and gutted of everything the API now does server-side:
`SCENARIOS`, `gauss`, `TWIN`, `corridorWest`/`corridorEast`/`seg`/
`offsetPoint`, `evalLeg`, `dpRoute`/`dpRoute0`, `weightsFromSliders`, the
old synchronous `optimise()`, `makeLiveScenario`, `inNogo` (only caller
was the deleted `evalLeg`), and the module constant `WEAR_POLICY` (never
a real request field — wear policy is vessel-spec-level server-side now).
What stays in that block: DOM-free helpers the UI script still needs
(`PORTS`, `LAND`/`inLand`, `NOGO`, `nmDist`, `bearing`, `idx10`) plus the
new API-calling shim below. One script tag, not two — both are "DOM-free
shared helpers," matching the block's own original comment, and keeping
one tag keeps the diff minimal per the ticket's "swap the block" framing.

New signature, same name (`optimise`) to minimise call-site diffs, two
dead parameters dropped (`scId`, `wear`):

```js
async function optimise(econ, comf, latestH) { ... }
// returns a Promise resolving to {plans, baseline, weights, missedWindow, latestH}
// (no `cands` — see Design 6)
```

Bootstrap constants at the top of the block (the one deliberately
committed secret, per the accepted decision — GitHub Pages has no
secret-injection mechanism, so any credential shipped here is effectively
public; scoped/throwaway, easy to rotate in one place):

```js
const API_BASE = "http://localhost:8000";  // point at a running `stingray planner`
const API_AUTH = { user: "stingray-demo", pass: "REPLACE-ME-ROTATE-OFTEN" };
```

Request body — everything not listed relies on the server's confirmed
defaults (same Antibes/Porto Cervo pair, same loaded vessel/speed grid
the demo already assumes, per `api/schemas.py`'s `PlanRequestIn`):
`{ pace: econ, comfort: comf, latest_arrival_h: latestH }`.

Submit → poll loop, one shared `apiFetch(method, path, body)` helper for
both:

```js
async function optimise(econ, comf, latestH) {
  const resp = await apiFetch("POST", "/v1/plans", { pace: econ, comfort: comf, latest_arrival_h: latestH });
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    const rec = await apiFetch("GET", `/v1/plans/${resp.job_id}`);
    if (rec.status === "done")   return mapPlanResult(rec.result, latestH);
    if (rec.status === "failed") throw new ApiError("job_failed", rec.error?.message ?? "planning failed");
    await sleep(1500);
  }
  throw new ApiError("timeout", "planning is taking longer than expected");
}
```

1.5s poll interval, flat (no backoff), 90s client-side deadline: matches
`docs/plans/ticket-B1.md`'s own stated design intent ("job durations are
in the seconds-to-tens-of-seconds sweet spot for a 1-2s poll interval")
and CLAUDE.md's empirically-confirmed ~19s cold / up to ~60s typical —
90s gives real margin above the documented worst case without polling
forever against a wedged server.

`apiFetch` classifies every failure into one `ApiError` kind consumed by
Design 3's error banner: `network` (fetch threw), `auth` (401),
`invalid_request` (422, server's own message surfaced verbatim),
`not_found` (404 on poll — an evicted/unknown job id), `job_failed`
(server-side failure, its own message surfaced verbatim), `timeout`
(deadline exceeded).

**Field mapping** (`mapCandidate(c)`, applied to every `result.candidates`
entry and to `result.baseline`) — the whole point of this shim is that
the ~450 lines of existing render/chat code barely change:

| New (`CandidateModel`) | Old vocabulary | Note |
|---|---|---|
| `duration_h` | `t` | direct |
| `fuel_kg` | `fuel` | **unit change, see below — display switches to kg** |
| `corridor_name` | `corr` | direct |
| `speed_kn` | `v` | direct |
| `comfort_index` | `comfort` | direct (still a raw accumulator; existing `idx10()` normalisation unchanged) |
| `wear_index` | `wear` | direct |
| `max_hs_m` | `maxHs` | direct |
| `active_engines` (1\|2) | `oneEngine` (bool) | `oneEngine = active_engines === 1` |
| `side` | `side` | direct |
| `distance_nm` | `dist` | direct |
| `score_eur` | `score` | direct |
| `track: [{lat_deg,lon_deg}]` | `track: [{lat,lon}]` | mapped once inside `mapCandidate`, not at every read site |
| `meets_eta_window`/`leg_targets`/`alteration_list` | *(none)* | not read by any existing UI code, carried through unused |

`result.weights` — confirmed by grep never read by any render/UI code
(only used inside the old `optimise()`'s own internal scoring) — no
mapping needed. `result.missed_window` → `missedWindow`.

**Fuel unit: display kg, not litres — recompute CO2 from
`co2_per_kg_fuel`, not `fuel_density_kg_per_l`.** Converting the real
`fuel_kg` back to litres via a density factor is a lossy, unnecessary
round-trip whose only benefit is label-stability, and every call site
touching `.fuel` is already being edited by this same field-mapping pass
regardless (`render()` lines 588/593/594, `basecard` line 608, `why()`
line 561, `renderVessel`'s CO2 tile line ~755, `answerChat`'s fuel/CO2
branches lines ~801/816-817) — no diff-size advantage to keeping litres,
and real units throughout is more honest, matching the same principle
already applied to dropping the synthetic weather scenarios. A one-time
`GET /v1/vessel` fetch at bootstrap (Design 4) populates a module-level
`let VESSEL_CO2_PER_KG` feeding all of these as `fuel_kg * VESSEL_CO2_PER_KG`.

### 2. Debounce + stale-response handling

Splitting `render()` into `requestPlan()` (submits a job, updates
`window._res`, calls `renderUI()`) and `renderUI()` (pure DOM rendering
from the cached `window._res`/`window._sel`/scrub time, no network) is a
direct, low-risk consequence of making `optimise()` a real network call —
and fixes a real inefficiency already present today (plan-card clicks and
the time-scrub slider currently re-run the full synchronous `optimise()`
for no reason, harmless when free, wasteful once it's a real job
submission):

| Trigger | Calls |
|---|---|
| `w_econ`/`w_comf` slider `input` | `requestPlan()`, **debounced 350ms** |
| `#arriveby` change, preset buttons | `requestPlan()`, no debounce (discrete choice) |
| `#tscrub` scrub, plan-card click, `setMode()` tab switch | `renderUI()` only — **no new job** |
| chat "arrive by HH:MM" | mutates `#arriveby`, `await requestPlan()` |
| page load | bootstrap sequence (Design 4) ending in `requestPlan()` |

350ms debounce (sliders only, via `clearTimeout`/`setTimeout`): short
enough to feel responsive on release, long enough to collapse a fast drag
gesture's dozens of `input` events into one submission.

Stale-response guard (every `requestPlan()` path, debounced or not): a
monotonic generation counter, the minimum mechanism that guarantees
correctness regardless of whether superseded network calls are actually
cancelled (an `AbortController` would also stop the wasted network
chatter — cut for scope, see Scope cuts, since the counter alone already
satisfies the correctness requirement):

```js
let _reqGen = 0;
async function requestPlan() {
  const myGen = ++_reqGen;
  showLoading();
  try {
    const res = await optimise(econVal(), comfVal(), latestHVal());
    if (myGen !== _reqGen) return;  // superseded, drop silently
    window._res = res; window._sel = 0;
    renderUI();
  } catch (err) {
    if (myGen !== _reqGen) return;
    showError(err);
  }
}
```

### 3. Loading/error UI states (new — none of this exists today)

**In flight:** inputs stay live/interactive throughout (deliberately not
disabled — disabling during a 1.5-60s job would break the continuous-
slider-drag UX this ticket must preserve, and the stale-response guard
already makes overlapping submissions safe). A small "Computing plan…"
status line near the plan cards shows while `_reqGen`'s in-flight request
is unresolved; the previous successful result stays visible underneath
throughout — no flash-to-empty on every slider tick. The one exception:
first page load has no prior result, so it gets its own empty-state
placeholder until bootstrap's first `requestPlan()` resolves.

**On error:** one shared banner (reusing the existing `missedWindow`
warning's visual pattern, `render()` lines 582-586) with copy keyed off
the `ApiError` kind — network/auth/invalid_request/job_failed/timeout,
each worded distinctly. The last successful result stays rendered
underneath, same non-destructive principle as loading. The one case with
no fallback is the initial bootstrap fetch failing (Design 4) — gets a
dedicated full-panel error state with a "Retry connection" button, since
fuel/CO2 rendering hard-depends on `VESSEL_CO2_PER_KG` being populated
first.

### 4. Weather-provenance display + bootstrap sequencing

The `#scenario` `<select>` (lines 121-128) is deleted outright. Replaced,
inside `#card-mission`, with a read-only line reusing the existing `.conf`
dashed-note styling: `Weather: {weather_source} · cycle {weather_cycle} ·
fetched {weather_fetched}`, from `GET /v1/health`.

Bootstrap sequence (replaces the bare `render()` call at line 864):
1. `Promise.all([GET /v1/vessel, GET /v1/health])`.
2. On success: populate `VESSEL_CO2_PER_KG`, populate the weather line,
   set the header tag ("REAL WEATHER · PROVISIONAL VESSEL MODEL" —
   `VesselSpecModel.provisional` stays honestly qualified since it's
   still `true`), then `requestPlan()` for the first time.
3. On failure: skip `requestPlan()` entirely, show the full-panel retry
   state (Design 3) — fail fast rather than half-render with `NaN`s.

`GET /v1/health` is fetched once at bootstrap plus a 5-minute
`setInterval` refresh (so a long investor demo session doesn't show a
visibly stale cycle timestamp) — **not** re-fetched per plan poll, since
weather cycles update on B1's hourly-to-3-hourly cron cadence, far
coarser than a demo session's interaction cadence.

### 5. The two remaining `SCENARIOS[scId]` call sites — resolved

Both currently do a live per-position weather lookup that has no
replacement once scenarios are dropped (there's no per-position/per-time
weather query in the new job-shaped API at all — by design, it doesn't
expose ad-hoc sampling, only whole-plan results):

- **`renderVessel`'s live-feed yaw simulation** (line 698): replaced with
  a fixed proxy built from the chosen candidate's own `r.maxHs` —
  `{hs: r.maxHs, wDir: (hdg+225)%360, waveDir: (hdg+225)%360}`. `hs` now
  reflects a real, server-computed value (arguably more meaningful than
  the old synthetic per-position lookup); direction is a constant,
  clearly-arbitrary quartering-sea offset rather than an invented
  position-varying function. This only feeds a demo-only illustrative
  animation already labelled "Live values simulated for demo" (line
  725, unchanged) — no new honesty gap.
- **`answerChat`'s "roughest" handler** (lines 803-808): simplified to
  report `r.maxHs` directly instead of walking the track. The old
  answer's "where" narrative detail is dropped, not faked — confirmed
  `LegTargetModel` has no per-leg hs field to reconstruct it from — a
  real, accepted information loss, noted as a candidate follow-up for the
  real bridge app (ticket 2.4), not built here.
- **`drawWx(scId,t)`, the chart heatmap/wind layer (amendment)** — the
  third call site, missed in the original research pass. Unlike the two
  above, this one has a real server-side replacement: `GET
  /v1/weather/field?h=N` (`api/weather_field.py`, new) returns a small
  downsampled grid (30×32, matching `OPERATING_AREA_BBOX`'s aspect ratio)
  built entirely on `core.weather.WeatherField.sample()` — the same
  interpolation the optimiser itself uses, no new `core/` code path.
  `h` is quantized server-side to the nearest whole hour
  (`quantize_hour()`) before sampling, both because source weather data is
  hourly-to-3-hourly resolution (serving sub-hour precision from a scrub
  slider would be fake precision) and because it makes the response
  ETag-cacheable across a continuous scrub gesture — many nearby scrub
  positions collapse onto the same served hour and same ETag, so the
  browser gets a `304` instead of a re-served ~1000-point grid.
  `drawWx(t)` becomes `async` (dropping the now-meaningless `scId` param):
  it fetches the field (via a small client-side cache keyed on the
  quantized hour, mirroring the server's own quantization so repeat scrubs
  within a session don't even hit the network), then paints the heatmap by
  iterating the grid's cells directly (one canvas rectangle per cell,
  sized to the cell's screen-space footprint) and the wind arrows by
  sampling every 3rd grid point — replacing the old per-pixel
  `SCENARIOS[scId](lat,lon,t)` continuous sampling with a coarser,
  server-truthful grid. Because this makes the draw path async where it
  was previously synchronous, `renderUI()` (Design 2) gets its own
  dedicated stale-response guard (a `_drawGen` counter, independent of
  `requestPlan()`'s `_reqGen`) so a slow weather-field fetch from an
  earlier scrub tick can never paint over a newer one during rapid
  scrubbing. A failed/unavailable weather-field fetch degrades gracefully
  — the heatmap/wind layer is skipped for that frame, land/routes/bathymetry
  still draw normally; this is chart decoration, not planning-critical.

### 6. `vstateRows`/`renderVessel` rework — candidates+baseline only, two tiles dropped

Every `res.cands` usage (three sites, grep-confirmed: lines 630/641/694)
replaced with a small pool: `const pool = [...res.plans, res.baseline];`
(2-4 points instead of ~16). Formulas are otherwise unchanged (`Math.min`
over the smaller pool). **Accepted, documented precision loss**: with
only 2-4 points, the same-side subset for the "Speed setpoint" comparison
can sometimes contain only the chosen candidate itself, trivially scoring
"100% optimal" — the direct, expected consequence of not touching
`core/optimiser.py`, called out here so it isn't mistaken for a bug
later.

`vstateRows()` drops the "Engine configuration" and "Added resistance"
tiles (their `TWIN.fuelRate` calls and the locals that fed them deleted
entirely) — shrinks from 7 tiles to 5: `[Speed setpoint, Engine load,
Sea-state exposure, Trim/ballast, Stabiliser mode]`. `renderVessel`'s
layout updates to the new indices (2 left-column tiles instead of 3, 2
right-column instead of 3 — `vbottom`'s 3 tiles are unaffected, none of
the removed tiles lived there); `hullStroke`, which read the now-deleted
Added-resistance tile's score, is repointed to `pCol(overall.p)` (overall
mission-fit) so it carries a distinct signal from `ringCol` (sea-state
exposure) rather than duplicating it. **No CSS changes needed** —
`.vwrap`/`.vside`/`.vbottom` are structural/flexbox, not tile-count-keyed
(checked directly against the `<style>` block).

### 7. CORS on the API side (`api/` — the one non-prototype change)

`api/config.py`'s `Settings` gains `cors_origins: tuple[str, ...] =
("http://localhost:8080",)`, with `from_env()` reading
`STINGRAY_CORS_ORIGINS` as a comma-split list (unset → the single-item
localhost default). `api/main.py`'s `create_app()` adds, before
`app.include_router(...)`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)
```

`allow_credentials=False` deliberately: Basic Auth travels in the
`Authorization` header, not a cookie, so CORS "credentials mode" (which
governs cookie semantics) isn't the relevant mechanism — this also keeps
`allow_origins` free to be an explicit list without the
`credentials=True`+`origins=["*"]` incompatibility ever coming up.
`allow_headers` names `Authorization` explicitly since it sits outside
the CORS "simple request" header set and needs naming for a non-wildcard
allow-list.

**Amendment:** `tests/test_api_cors.py`'s preflight/real-request tests are
parametrized over `["/v1/health", "/v1/weather/field"]` rather than
covering only `/v1/health` — `drawWx()`'s new endpoint (Design 5's
amendment) is exactly the kind of cross-origin GET this middleware exists
to unblock, so it gets the same CORS coverage as every other route.

### 8. Demo deploy workflow

Both `prototype/deploy/github-pages.yml` (template) and
`prototype/.github/workflows/deploy.yml` (the live one — confirmed
differing path prefixes reflect the same file used two ways, otherwise
identical in shape) need the same two edits in lockstep: remove the
`schedule: cron` trigger, remove the `fetch_weather.py` step. No new
deploy step is needed for the API connection — `API_BASE`/`API_AUTH` are
plain committed JS constants (static GH Pages has no build-time secret
injection to wire up, and none is being introduced). Delete
`prototype/livewx.js` and `prototype/ingest/fetch_weather.py` outright
(dead once scenarios are dropped). Leave `prototype/ingest/
fetch_grib_nomads.py` untouched — confirmed neither workflow invokes it;
it's a separate reference artefact per `prototype/README.md`.

### 9. File organisation

Unchanged conventions: single file, no build step, no bundler/module
system. All edits land inside `prototype/stingray_planner.html`'s
existing two script blocks plus small DOM/`<style>` tweaks (dropped
`#scenario` select, new weather-provenance line). `prototype/index.html`
(pure redirect) needs no change.

### 10. Queue-depth cap on `POST /v1/plans` (amendment)

`api/config.py`'s `Settings` gains `max_queue_depth: int = 20`. `api/
jobs.py`'s `JobStore.submit()` counts in-flight (`queued`/`running`)
records before accepting a new one; over the cap, raises a new
`QueueFullError`, mapped by a registered exception handler in `api/
errors.py` to `429` with `{code: "queue_full", message}` and a
`Retry-After: 5` header. The counting subtlety worth recording:
`JobRecord.status`'s property only reflects a completed `Future` once
`_refresh()` has actually run (it sets `finished_at`) — counting naively
over `self._records` without refreshing first would misclassify a
completed-but-never-polled job as still `"queued"` and overcount. `submit()`
refreshes every record under its lock before counting, closing that gap;
`tests/test_api_jobs.py` has a dedicated regression test for it
(`test_submit_refreshes_before_counting_so_a_completed_unpolled_job_does_not_overcount`).
This is a distinct bound from the existing TTL/`job_max_size` eviction
sweep (design already in ticket B1) — that one bounds total stored record
count over time; this one bounds concurrent in-flight work against the
`ProcessPoolExecutor`, protecting it from an unbounded submission
backlog (e.g. a slider-drag bug, or a hostile client, submitting far
faster than jobs complete).

## Tests

No new JS test framework — the prototype has zero existing JS test
coverage, and this project's established convention (tickets 0.5/0.8) for
this kind of real-integration surface is "verify for real, once,
documented," not stand up new test infra for a pre-production demo.

**Required manual, real-browser verification** (documented, same
discipline as ticket 0.5/0.8's "unverified-by-me, needs one real run"):
start a real local API instance (`STINGRAY_CORS_ORIGINS=http://localhost:8080
uvicorn api.main:create_app --factory --port 8000`) and serve
`prototype/` on a *different* local port (essential to actually exercise
CORS, not accidentally same-origin) — then in a real browser: confirm
bootstrap populates the weather line/header tag and the first plan
renders; rapid-drag a slider and confirm exactly one request fires ~350ms
after release with no stale flicker; rapid-switch `#arriveby`/presets and
confirm the UI settles on the *last* selection only (check the Network
tab for the overlap, not just that the UI looks right); confirm plan-card
click and `#tscrub` scrub fire zero new requests; kill the API mid-drag
and confirm the network-error banner appears with the prior result intact
underneath; restart with wrong credentials and confirm a distinct
auth-error message; ask the chat "Where is it roughest?" and an "arrive
by" question end-to-end; resize/toggle the UNDERWAY tab and confirm the
5-tile vessel-state layout renders cleanly.

**Automated, CI-run — `api/` only** (the one real server-side code
change): new `tests/test_api_cors.py` (own file, matching this repo's
one-concern-per-`test_api_*.py`-file convention) using
`fastapi.testclient.TestClient`: a preflight `OPTIONS` from a configured
origin returns matching `Access-Control-Allow-Origin` and includes
`Authorization` in `Access-Control-Allow-Headers`; the same preflight
from a non-configured origin gets no `Access-Control-Allow-Origin` back
(default-deny, not a wildcard); a real `GET /v1/health` with an `Origin`
header from a configured origin echoes it on the actual response too;
`Settings.from_env()`'s `STINGRAY_CORS_ORIGINS` parsing (unset → default,
comma-joined → correctly split/stripped tuple).

## Scope cuts (explicit)

- No real cloud VM deployment/provisioning — code + config only.
- No new JS test framework — real-browser manual verification only.
- No vessel/origin/destination picker UI — omitted per the server's
  confirmed matching defaults.
- No chat tool-calling rework — `answerChat`'s regex/keyword dispatcher
  stays structurally as-is; ticket 2.4's job.
- No restoring full-fidelity candidate-pool vessel-state comparisons, no
  client-side twin what-ifs — both explicitly dropped (Design 6/the two
  dropped `vstateRows` tiles).
- No multi-tenant auth — one shared demo credential, matching B1's own
  stopgap framing.
- No changes to `core/`/`ingest/`/`capture/` — `api/` gets only the CORS
  addition (Design 7).
- No `AbortController`-based cancellation of superseded network calls —
  the generation-counter guard already guarantees UI correctness; actual
  cancellation is a real but optional efficiency improvement.
- No per-leg/"where" weather-peak reconstruction in chat — the API
  schema genuinely doesn't carry the data; a real, flagged follow-up.
- No preserving the "Live" weather scenario in any form — real server
  weather replaces it entirely, `livewx.js`/`makeLiveScenario`/the fetch
  cron are deleted, not kept as a dead option.

## Docs

- `ROADMAP.md`: mark B2 done, summarising the field-mapping/unit
  decisions (kg display) and the scenario-dropdown removal.
- `CLAUDE.md`: new gotcha entries for (1) the request-generation-counter
  stale-response pattern (first real async/stale-response concern in this
  repo's JS — worth flagging as reusable for ticket 2.2's real bridge
  app), (2) the accepted precision loss in candidates+baseline-only
  vessel-state comparisons (so it isn't mistaken for a bug later), (3)
  the CORS `allow_credentials=False` + Basic-Auth-via-header reasoning,
  in case a future ticket changes the auth mechanism.
- `prototype/README.md`: drop the `livewx.js`/`fetch_weather.py`/"Live"
  scenario bullets; note the demo now requires a running planner API
  instance to function at all (no longer fully offline-capable).
- `prototype/deploy/HOSTING.md`: remove the stale 6-hourly weather-fetch
  sentence; add the `STINGRAY_CORS_ORIGINS` requirement for whoever
  stands up a real API instance to point the deployed demo at; **amendment:**
  also add a mixed-content note — the deployed demo is served over HTTPS
  (GitHub Pages), and a browser silently blocks an HTTPS page from
  fetching a plain-HTTP API, so `API_BASE` in `stingray_planner.html`
  must be updated to the HTTPS cloud URL before/at deploy time, not left
  at the `http://localhost:8000` dev default.

## Verification

- `pytest -m ""` green including the new `tests/test_api_cors.py`;
  `ruff check .` clean.
- The full manual real-browser checklist above, run for real against a
  locally-running B1 API instance before considering B2 done — this
  ticket's entire value is a real, working browser↔API integration, which
  a unit test can't meaningfully stand in for.

### Critical files

- `prototype/stingray_planner.html` — the whole prototype-side change.
- `api/config.py`, `api/main.py` — the CORS + queue-depth-cap additions.
- `api/weather_field.py`, `api/routes.py`, `api/schemas.py` — the
  amendment's `GET /v1/weather/field` endpoint.
- `api/jobs.py`, `api/errors.py` — the amendment's queue-depth cap.
- `prototype/deploy/github-pages.yml`, `prototype/.github/workflows/deploy.yml` — deploy workflow edits (lockstep).
- `prototype/README.md`, `prototype/deploy/HOSTING.md` — doc updates.
