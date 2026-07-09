# Ticket B1 — Planner + logging service: job-shaped API, cross-platform installer, NMEA 2000 gateway abstraction

## Context

Phase 0 (tickets 0.1–0.8) is done: `core/` has a working, tested optimiser
(`core.optimiser.optimise`) over real geography and real weather, but no
user-facing surface — the hosted demo still runs a synthetic JS brain.
ROADMAP.md originally scoped B1 narrowly: "API service: FastAPI wrapper over
`core.optimiser.optimise` + scheduled GRIB fetch | Cheap single VM,
password-protected."

**B5 was amended today (July 2026)** and changes B1's shape materially: the
MVP deployment target is now the yacht's **bridge PC**, running a **planner
service + logging service installed locally from a single, cross-platform
installer** (core is pure Python). The amended B5 row in full:

> MVP deployment target is the yacht's bridge PC — planner service +
> logging service installed locally (single installer; core is pure Python,
> cross-platform), with an NMEA 2000 gateway as the entire hardware BOM —
> preferred: Yacht Devices YDEN-02 Ethernet gateway (bus-powered, no host
> drivers, serves any listener on the ship LAN — logging isn't hostage to
> one PC or USB port), with Actisense USB (NGT-1/NGX-1) supported as the
> alternative for vessels where LAN access at the backbone isn't practical.
> Ingestion layer must abstract over both (Ethernet TCP/UDP stream vs
> serial-over-USB). IMU: optional early add, not committed — the
> ingestion/telemetry schema should accept a motion source if present
> (tier-flagged), but nothing in MVP scope depends on it. Heavy weather
> ingest stays cloud-side (vessel syncs compact npz; planning stays
> offline-capable on last-synced weather). Known deferrals, stated not
> hidden: no IMU → comfort model stays priors-only until Phase 2;
> always-on logging depends on the PC staying up — sleep/reboot gaps are
> the main data risk. Dedicated edge box remains the full-tier product,
> Phase 2.

Per direct instruction, this plan folds B5's amendment into B1: **B1 now
delivers both services, packaged as one cross-platform installer, with the
cloud VM running the identical artefact** (not a separately-built Docker
image). This absorbs the gateway-abstraction and telemetry-schema work B5
describes; it does **not** absorb ticket 1.3's "first real encounter with
PGN variability — budget time" — that's real-vessel bus-data messiness,
only reachable once real hardware/vessels exist (Phase 1). B1 builds the
transport-layer abstraction and a real (not stubbed) PGN decode path against
a maintained, canboat-database-backed library; validating it against a
live, idiosyncratic vessel installation is 1.3's job, flagged here as
pending exactly like ticket 0.5 flagged its live-GRIB verification gap.

**Four contract points, hard requirements, addressed section-by-section
below:**
1. Plans are jobs, not request/response — submit → job id → poll/subscribe;
   internals can be a process pool, but the contract is job-shaped from day
   one so the bridge app UI never notices internal changes.
2. Location transparency — one API surface, identical whether running on
   the pilot cloud VM or locally on a bridge PC; the UI never cares which.
3. Versioned pydantic schemas mirroring `PlanRequest`/`Candidate`/
   `VesselSpec`, with drift from `core/` caught automatically, not by
   developer discipline.
4. Interactive planning only — telemetry sync stays store-and-forward
   messaging per `TECHNICAL_ARCHITECTURE.md` §7; no REST creep into that
   channel.

**This plan makes zero changes to `core/` source files** — everything here
wraps existing, unmodified functionality, keeping B1 unambiguously outside
ROADMAP's optimiser feature freeze (bridge section, pending ticket 1.5).

**Three additions from plan review**, folded into the relevant design
sections below and called out again in Tests: (1) SQLite WAL + busy_timeout
on both processes' connections, with a concurrent read/write test — design
7/13; (2) the weather pool-swap must run off the event loop, no request
stalls during `shutdown(wait=True)` — design 5; (3) TTL/max-size eviction
on the in-memory job store, since bridge PCs run for weeks without a
restart — design 2/13.

**Implementation order:** first step is confirming the `nmea2000` package's
real availability/version — done during planning: PyPI confirms it's real,
actively maintained (86 releases), current version `2026.5.2`,
`requires_python>=3.11` (matches this project's floor exactly, no
conflict); `pyserial` confirmed at stable `3.5`. Design 12's version floors
below reflect this. The pywin32-vs-NSSM choice for the Windows service
wrapper (design 11) is deliberately deferred to the early PyInstaller trial
build, not decided here.

## Design

### 1. Package layout — two new top-level packages, one deploy dir

Following the existing one-way layering (`fit`/`ingest` depend on `core`,
never the reverse): `api` and `capture` both depend on `core`, never the
reverse; `core`/`ingest` gain no new imports.

- **`api/`** — the planner service (job-shaped `optimise()` wrapper):
  `main.py` (app factory + lifespan), `config.py` (env-driven settings incl.
  `STINGRAY_ROLE=cloud|vessel`), `state.py` (singleton construction +
  weather hot-swap), `jobs.py` (job store + process pool), `schemas.py`
  (pydantic mirrors + `SCHEMA_VERSION`), `convert.py` (hand-written
  dataclass↔pydantic conversion), `routes.py`, `auth.py`, `errors.py`,
  `weather_sync.py` (role-conditional: cloud side serves the npz, vessel
  side pulls it — design 6).
- **`capture/`** — the logging service (NMEA 2000 ingestion, entirely new):
  `gateway.py` (the `GatewayReader` protocol + `YachtDevicesEthernetGateway`
  + `ActisenseSerialGateway` + `ReplayGateway`, design 7), `pgn.py` (thin
  wrapper isolating the third-party PGN-decode dependency), `telemetry.py`
  (canonical `TelemetrySample` schema, tier-flagged optional motion/IMU),
  `store.py` (local SQLite append-only store), `service.py` (the daemon
  loop: gateway → PGN decode → normalise → store).
- **`deploy/`** — packaging, genuinely new ground with no precedent to nest
  under: `pyinstaller.spec`, `windows/installer.iss` (Inno Setup),
  `macos/build_pkg.sh` + launchd plists, `linux/install.sh` + systemd unit
  files, `.env.example`, `README.md` (the deployment runbook).
- New tests: `tests/test_api_schema_parity.py`, `test_api_jobs.py`,
  `test_api_routes.py`, `test_api_auth.py`, `test_api_weather_sync.py`,
  `test_capture_gateway.py`, `test_capture_pgn.py`, `test_capture_telemetry.py`,
  `test_capture_store.py`.

### 2. Job execution model (contract point 1) — unchanged in shape from prior scoping

`concurrent.futures.ProcessPoolExecutor`, sized to `os.cpu_count()`,
constructed in `api/main.py`'s lifespan. No Celery/Redis — the "queue" is
the executor's own task queue plus an in-memory `dict[str, JobRecord]`
guarded by a lock. `ProcessPoolExecutor` (not `ThreadPoolExecutor` or
FastAPI `BackgroundTasks`): `optimise()` is CPU-bound single-threaded
Python, so threads wouldn't parallelise across cores, and `BackgroundTasks`
has no natural home for job-id-keyed pollable status — a dedicated executor
+ job table is the smallest genuinely job-shaped mechanism.

- `POST /v1/plans` — server validates by constructing a real
  `core.optimiser.PlanRequest` synchronously (fail-fast, see design 10),
  generates a `uuid4().hex` job id, submits to the executor, returns `202`
  + `{job_id, status: "queued"}`.
- `GET /v1/plans/{job_id}` — `{job_id, status, submitted_at, started_at,
  finished_at, result: PlanResultOut | null, error | null}`. Lifecycle
  `queued → running → done | failed`.

**Location transparency, concretely satisfied here:** single global job
dict, single global vessel spec (no multi-tenant data model to strip out
later — matches the one shipped `data/vessel_specs/mys_50m_default.yaml`);
zero outbound network calls in the request/job path itself; bind
address/port/pool size/data paths are all `api/config.py` env vars, so
`stingray-planner` (design 11) is the same invocation on a Frankfurt VM or
a bridge PC's engine-room-adjacent nav station.

**Job-store eviction (addition — bridge PCs run for weeks, not restarted
daily like a typical cloud deploy):** the in-memory `dict[str, JobRecord]`
is unbounded by default and would leak for the life of the process
otherwise. A periodic asyncio background task (same pattern as design 5's
hot-swap watcher) sweeps every few minutes: records past a configurable TTL
(default 24h) after `finished_at` are dropped; a hard `max_size` cap
(default a few thousand) is a backstop, evicting oldest-`finished_at`
records first once exceeded. Only `done`/`failed` jobs are eligible —
`queued`/`running` jobs are never evicted regardless of age.

### 3. Pydantic schema mirrors + versioning (contract point 3) — unchanged in shape

Field-for-field mirrors in `api/schemas.py` against `core.optimiser`'s
`PlanRequest`/`Candidate`/`LegTarget`/`Alteration`/`PruneDiagnostic`/
`PlanResult` and `core.vessel_spec.VesselSpec`'s nested dataclasses — no
`to_dict`/serializer exists in `core/` to derive from, so these are
hand-written. `PlanRequestIn` omits `weather`/`geography` (server-side
singleton state, design 5) and takes `vessel: VesselSpecModel | None =
None` (falls back to the loaded default).

**Versioning:** `/v1/...` URL prefix for breaking route changes; a
`SCHEMA_VERSION` semver constant in `api/schemas.py`, surfaced on
`GET /v1/health`, for payload-shape changes.

**Drift detection (the concrete, CI-enforced mechanism):**
`tests/test_api_schema_parity.py` introspects `dataclasses.fields()` on
each core dataclass and `ModelClass.model_fields` on its pydantic mirror,
asserting matching field-name sets modulo an explicit, in-test
`INTENTIONAL_DIFFERENCES` allow-list. Runs under `pytest -m ""`, which CI
already runs (once `.github/workflows/ci.yml`'s install step gains the new
`api`/`capture` extras) — a developer who adds a field to `VesselSpec` and
forgets the mirror gets a red CI run, not a silent gap.

### 4. Endpoint surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/plans` | Submit `PlanRequestIn`; `202` + job descriptor, or `422` fail-fast |
| GET | `/v1/plans/{job_id}` | Poll job status/result; `404` if unknown |
| GET | `/v1/health` | Liveness, `SCHEMA_VERSION`, weather provenance (cycle/fetched/source), `STINGRAY_ROLE` |
| GET | `/v1/vessel` | Read-only: the currently loaded default `VesselSpec` |
| GET | `/v1/weather/latest.npz` | **Cloud role only.** Serves the current compact npz; `ETag`/`Last-Modified` for conditional GET (design 6) |
| GET | `/v1/telemetry/status` | Read-only view into `capture/`'s local store: last-sample timestamp, sensor tier, gap detection (design 7/8) |

**Explicitly out of scope:** no `DELETE /v1/plans/{id}`; no
`/v1/telemetry` write/ingest endpoint of any kind (contract point 4 —
telemetry only ever gets *read* back via the one status endpoint above;
writes happen locally within `capture/`, never over HTTP); no
`/v1/vessels/{id}` multi-tenant routing; no session/login endpoints; no
WebSocket/SSE (justified in Scope cuts).

### 5. Shared-state design + role config

`api/state.py`, constructed once in `api/main.py`'s lifespan:
`geography = RealGeography()`, `weather = GriddedWeatherField.from_npz(...)`,
`vessel = VesselSpec.from_yaml(...)`. `core/legs.py`'s `_navigable_along_leg`/
`_leg_depth_ok` are `@lru_cache`d keyed on `(p, q, geography)` including the
instance itself — reusing the same objects across requests is what lets
that cache pay off cumulatively, and it's safe since everything in `core/`
is stateless per request.

**Process-pool wrinkle:** worker processes don't share the parent's
`lru_cache`/app state. `ProcessPoolExecutor(initializer=_worker_init,
initargs=(config,))` constructs `geography`/`weather`/`vessel` once **per
worker process** into worker-global state — not passed per-task (which
would re-pickle ~1MB of bathymetry/weather grids every job).

**`STINGRAY_ROLE=cloud|vessel`** (`api/config.py`) is the one flag that
makes the identical codebase behave correctly on either deployment target
(concretely satisfying contract point 2):
- `cloud`: crontab triggers `ingest.fetch_grib_*` (heavy, needs eccodes);
  `GET /v1/weather/latest.npz` is live; no outbound weather sync task runs.
- `vessel`: no `ingest` extras installed at all (see design 11 — this is
  also what makes the Windows/macOS build tractable); a background
  opportunistic-pull task (design 6) is the only thing that updates the
  local npz.

**Hot-swap on weather change, either role:** a lightweight periodic mtime
check on the configured local npz path (asyncio background task); on
change, gracefully retire the current `ProcessPoolExecutor`
(`shutdown(wait=True)`, letting in-flight jobs finish against the weather
they started with) and spin up a replacement whose initializer loads the
now-current file. **This single mechanism serves both roles identically** —
it doesn't care whether the file changed because cron+ingest wrote it
(cloud) or a background download replaced it (vessel); only the upstream
writer differs.

**Off-event-loop requirement (addition):** `ProcessPoolExecutor.shutdown(
wait=True)` blocks the calling thread until every in-flight job finishes —
if called directly from the asyncio event loop (as the mtime-check task
naturally would be), it stalls every other request (`GET /v1/plans/{id}`
polls, `/v1/health`, the vessel-role sync pull) for however long the
oldest in-flight job takes to finish, which defeats the point of the
job-shaped API during exactly the window it matters most. The mtime-watch
task must hand the actual swap off via `asyncio.to_thread(old_pool.shutdown,
wait=True)` (or an equivalent single-purpose thread), await it there, and
only then publish the new pool/executor reference — the event loop keeps
serving every other route the entire time the old pool drains.

### 6. Weather distribution: cloud-side heavy ingest, vessel-side compact sync

Heavy GRIB ingest (`ingest.fetch_grib_nomads`/`fetch_grib_ecmwf`, cfgrib,
needs system `eccodes`) runs **cloud-side only**, via crontab
(`deploy/linux/crontab.example`) invoking the existing, unmodified CLIs at
their real cadences (NOMADS hourly-ish, ECMWF 3-hourly, per ticket 0.5),
writing straight to the local npz path `api/state.py` already watches (no
new code in `ingest/`).

The bridge PC never runs `ingest.*` — it pulls the already-cropped, compact
npz from its configured cloud endpoint. `api/weather_sync.py`
(vessel-role only): an asyncio background task, on a coarse interval (e.g.
every 15 min, configurable) and tolerant of failure (opportunistic —
"expensive, intermittent satcom" per PRODUCT_SPEC §7), issues a
conditional `GET {cloud_url}/v1/weather/latest.npz` with
`If-Modified-Since` from the last successful pull; a `304` is a no-op, a
`200` writes the new bytes atomically to the local npz path and lets
design 5's existing hot-swap watcher pick it up — **no separate reload
path for the sync case**, it feeds the exact same mechanism cron feeds on
the cloud side. Auth: the same shared-secret Basic Auth as every other
endpoint (design 9) — the vessel authenticates to its own cloud instance
exactly as the UI would.

This is also what makes cross-platform packaging tractable: bundling
cfgrib/eccodes (a C library, not pip-installable, notoriously fragile to
bundle cross-platform) into a Windows/macOS PyInstaller build is avoided
entirely — the vessel-role build never imports `ingest.*`.

### 7. NMEA 2000 gateway abstraction (new — B5's "logging service")

**`capture/gateway.py`** — one `Protocol`:

```python
class GatewayReader(Protocol):
    def raw_frames(self) -> Iterator[RawFrame]: ...  # blocking iterator, one CAN/N2K frame at a time
```

Two real implementations plus a test double, all yielding the same
`RawFrame` (PGN + priority + source/dest address + 8-byte data + timestamp):
- **`YachtDevicesEthernetGateway`** (preferred, per B5) — connects to the
  YDEN-02's **RAW-mode TCP/UDP data server** (documented in the device's
  user manual, Appendix E "Format of Messages in RAW Mode"; Yacht Devices
  also publish a reference Python module, "Python Gateway Programming
  Manual" / `mod_nmea2000.html`, worth reviewing as an implementation
  reference during build, not a dependency). Bus-powered, no host drivers,
  serves any listener on the ship LAN — matches B5's stated preference
  reasoning (logging isn't hostage to one PC/USB port).
- **`ActisenseSerialGateway`** (alternative) — serial-over-USB at
  115200/230400 baud, Actisense's documented binary framing (canboat's
  wiki has a maintained reference: `canboat/canboat/wiki/actisense-serial`).
  Needs `pyserial`.
- **`ReplayGateway`** (test double, matches this project's established
  `Synthetic*`/`Real*` pairing convention — e.g. `SyntheticGeography`/
  `RealGeography`) — replays a fixture file of canned `RawFrame`s, so
  `capture/service.py`'s full pipeline is testable without hardware.

**`capture/pgn.py`** — thin wrapper around the **`nmea2000`** PyPI package
(canboat-PGN-database-backed encoder/decoder, pure Python, actively
maintained) rather than hand-rolling a PGN table — isolating the dependency
behind one module means it's swappable if a better-maintained option
appears later, and keeps the canboat-database dependency (the same
reference database the marine-electronics OSS community maintains) out of
`gateway.py`'s transport-only concern. **Flagged, not hidden:** PGN
coverage against a real, idiosyncratic vessel installation is unverified
here — that's ticket 1.3's "first real encounter with PGN variability,"
this ticket only proves the decode path works against known-good fixture
frames.

**`capture/telemetry.py`** — canonical `TelemetrySample`: position, SOG/COG,
STW (where available), heading, engine fuel-rate/RPM/load per CLAUDE.md's
graceful-degradation tiers ("full integration / NMEA + engine fuel rate /
NMEA + manual fuel"), plus **`motion: MotionSample | None = None`**
— optional, tier-flagged, exactly matching B5's "accept a motion source if
present, nothing depends on it." A `sensor_tier: Literal[...]` field on
every sample makes degradation explicit and queryable, not inferred.

**`capture/store.py`** — SQLite, append-only, one table keyed on
(timestamp, pgn) — the natural single-PC-scale equivalent of the "local
store" ticket 1.3 later builds out fully; boring, zero-ceremony, matches
this project's existing "boring tech" bias.

**Concurrent access (addition):** the same SQLite file is written by
`capture/service.py` (continuous) and read by the planner process's
`GET /v1/telemetry/status` handler (design 4) — two independent OS
processes, per design 8's process-topology decision. Every connection
opened by either process sets `PRAGMA journal_mode=WAL` (readers don't
block the writer, and vice versa) and `PRAGMA busy_timeout=5000` (a writer
momentarily contending with WAL's checkpoint, or two near-simultaneous
opens, retries for up to 5s instead of raising `database is locked`
immediately) — set once, in one shared connection-opening helper both
processes call, not duplicated per call site.

### 8. Process/service topology — two independent processes, not one

**Considered:** a single process running both the HTTP planner API and the
continuous bus-logging loop, vs. two independent long-running processes
sharing only the local SQLite store. **Recommendation: two processes.**
B5's own text flags "always-on logging depends on the PC staying up" as the
main data risk — continuous telemetry capture should not share a failure
domain with the planner API (which restarts on deploys, can be killed by a
hung request, etc.). Decoupling means a planner-service restart never
interrupts logging, and vice versa; `capture/service.py`'s status is
exposed read-only through the planner's `/v1/telemetry/status` (design 4)
by reading the same SQLite file, not via an in-process call — no second
HTTP surface to secure/package. "Single installer" (B5's wording) is
satisfied at the packaging layer (design 11), not by merging the processes.

### 9. Auth — unchanged in shape, TLS now role-conditional

HTTP Basic Auth (`fastapi.security.HTTPBasic`), single shared
username/password from env vars, `secrets.compare_digest`, applied to every
endpoint uniformly. Explicitly a stopgap, not tenancy (no per-vessel
identity/roles — that's ticket 1.4).

**TLS is role-conditional, not universal** (new, follows from the bridge-PC
target): the cloud role sits behind a reverse proxy with automatic TLS
(Caddy — a few lines of config, still "cheap," not IaC-scale), since it's
public-internet-facing and `docs/pilot/data-agreement-outline.md` commits
to encryption in transit. The vessel role binds to the ship LAN interface
(or localhost) only, plain HTTP — matching `TECHNICAL_ARCHITECTURE.md` §8's
stated principle for the target edge box ("no internet exposure of the
edge device"), which applies equally to the bridge-PC MVP: it's never
meant to be reachable from the public internet, so a public TLS cert
doesn't apply, and Basic Auth over LAN-only HTTP is a reasonable stopgap
matching what's actually being protected against (not a public-internet
threat model).

### 10. Error handling — unchanged in shape

`PlanRequestIn → PlanRequest` conversion happens synchronously in the
`POST /v1/plans` handler, before job submission — `core.optimiser.
_validate_endpoint`'s checks (bare `ValueError`, and `OutOfOperatingAreaError`
which subclasses it) are cheap and don't need deferring. A registered
`@app.exception_handler(ValueError)` returns `422` reusing the exception's
own message. Failures inside a worker (post-construction) surface via
`Future.result()` when polled, classified the same way into the job's
`error` field; the poll itself still returns `200` (job-level failure, not
transport failure). `core/`/`ingest/` stay completely untouched;
`api/`/`capture/` are the first packages in this repo with a legitimate
reason for `import logging` (stdlib, `basicConfig` at startup).

### 11. Packaging & deployment — PyInstaller, one artefact, three OS targets

**One PyInstaller build, subcommand-dispatched**, not two separate
executables: a single `stingray` binary (`deploy/pyinstaller.spec`) with
`stingray planner` / `stingray capture` subcommands — simpler to build (one
`Analysis` block, one set of bundled deps) than maintaining two frozen
apps, and still satisfies "two processes" (design 8) since each subcommand
is invoked as its own long-running OS process/service pointing at the same
binary. PyInstaller **cannot cross-compile** — build separately per target
OS (confirmed: bundles a native interpreter per-platform), so this needs a
3-way CI matrix (windows-latest/macos-latest/ubuntu-latest runners, which
GitHub Actions provides natively) in a new, manually/tag-triggered workflow
(`deploy/build-installers.yml` — separate from `.github/workflows/ci.yml`,
matching this repo's existing precedent of `prototype/deploy/github-pages.yml`
being its own deploy-specific workflow, not folded into main CI). Known
sharp edge (found in research, not yet hit here): uvicorn+FastAPI+pydantic
need explicit `hiddenimports` in the spec file or PyInstaller silently
drops dynamically-imported modules — budget a real trial build early, not
at the end.

- **Windows**: PyInstaller `.exe` → **Inno Setup** installer script
  (`deploy/windows/installer.iss`) registering both subcommands as Windows
  services (via a small service wrapper — `pywin32`'s `win32serviceutil` or
  NSSM wrapping the two `stingray.exe planner`/`stingray.exe capture`
  invocations), auto-start on boot, restart-on-failure.
- **macOS**: PyInstaller binary → `.pkg` (`pkgbuild`/`productbuild`) installing
  two `launchd` `.plist`s (`/Library/LaunchDaemons` — always-on even when no
  user is logged in, matching "always-on logging") with `KeepAlive` for
  crash-restart.
- **Linux (cloud VM)**: same PyInstaller binary (Linux build) + a plain
  `install.sh` (curl-and-run, no GUI installer needed for a server) that
  installs two systemd units (`stingray-planner.service`,
  `stingray-capture.service` — the latter inert/unused on the cloud VM,
  since there's no NMEA bus attached there; started conditionally on
  `STINGRAY_ROLE`), plus Caddy for TLS (design 9).

**Why this satisfies "the cloud VM hosting is the same artefact deployed
shore-side" concretely:** the Linux build is produced by the identical
`pyinstaller.spec`/codebase as the Windows/macOS builds — only the
OS-native service-registration wrapper (Inno Setup vs pkgbuild vs
systemd-install-script) differs, exactly matching contract point 2's "same
artefact, moving it is a deployment change."

### 12. `pyproject.toml` changes

```toml
api = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "pydantic>=2.7"]
capture = ["nmea2000>=2026.5", "pyserial>=3.5"]
```
(`nmea2000` and `pyserial` version floors confirmed live against PyPI
during planning — see "Implementation order" in Context — real current
versions `2026.5.2`/`3.5` respectively, not guessed.)
`ingest` extras unchanged, untouched — only ever installed/exercised at
`STINGRAY_ROLE=cloud` runtime (and in CI, for `ingest`'s own existing
tests). `[tool.hatch.build.targets.wheel].packages` gains `"api"`,
`"capture"`. `.github/workflows/ci.yml`'s install step becomes
`pip install -e ".[dev,api,capture]"` (no `eccodes` system dependency
needed for this — `capture`'s deps are pure-Python/pip-installable) so the
new tests actually run under CI.

### 13. Tests

**Automated, CI-run, no hardware/network/deployment needed:**
- `test_api_schema_parity.py` — the drift-detection mechanism (design 3).
- `test_api_jobs.py` — job-store lifecycle (queued→running→done/failed,
  concurrent submissions, id uniqueness) using `SyntheticGeography`/
  `SyntheticWeatherField` + a small speed grid so real `optimise()` calls
  finish fast enough for a unit test. **Addition:** eviction tests —
  a `done`/`failed` record past the configured TTL is swept; a record
  under TTL survives; `queued`/`running` records are never evicted
  regardless of injected age; the `max_size` backstop evicts
  oldest-`finished_at` first once exceeded.
- `test_api_routes.py` — FastAPI `TestClient` contract tests across the
  full endpoint table (design 4), incl. `422`/`404` shapes.
- `test_api_auth.py` — missing/wrong credentials → `401` everywhere.
- `test_api_weather_sync.py` — conditional-GET/`ETag` behaviour against a
  local fake cloud endpoint; hot-swap fires on a `200`, no-ops on `304`.
- `test_capture_gateway.py` — `ReplayGateway` yields fixture `RawFrame`s in
  order; `ActisenseSerialGateway`/`YachtDevicesEthernetGateway` unit-tested
  against a mocked socket/serial port, not real hardware.
- `test_capture_pgn.py` — `nmea2000`-backed decode against a handful of
  known-good fixture frames (position PGN 129025, engine PGN 127488, etc.
  — sourced from canboat's published sample logs).
- `test_capture_telemetry.py` — tier-flagging: a sample with/without
  `motion` gets the right `sensor_tier`.
- `test_capture_store.py` — SQLite append/read round-trip. **Addition:**
  a concurrent read/write test — a writer thread/process appending rows in
  a loop while a reader (in a second connection, ideally a second process
  to actually exercise cross-process WAL/busy_timeout behaviour, not just
  cross-thread) polls concurrently for a fixed duration; assert zero
  `database is locked` errors and that every written row is eventually
  visible to the reader.
- `test_api_weather_sync.py` gains a case for the off-event-loop
  requirement — a fake slow `shutdown(wait=True)` (a stub pool whose
  shutdown sleeps) is triggered by a simulated npz change, and a concurrent
  request against an unrelated route (e.g. `/v1/health`) must complete
  before the slow shutdown does, proving the swap didn't block the loop.

**Documented manual-verification steps** (matching ticket 0.5's
"unverified-by-me, needs one real run" precedent):
- Real deployment smoke test per OS build: install, hit `/v1/health`,
  submit a real plan against `RealGeography`, poll to completion.
- Real gateway smoke test: one real YDEN-02 and/or one real Actisense unit,
  confirm `capture/service.py` logs real frames to SQLite — genuinely
  needs hardware, flagged as pending until B5's kit arrives.
- Real weather-sync smoke test: cloud role fetches live, vessel role pulls
  it, confirm the hot-swap fires end-to-end.
- Real TLS check (cloud role): Caddy obtaining a real cert against a real
  domain.

## Scope cuts (explicit)

- **No telemetry write/ingest endpoint over HTTP** (contract point 4) —
  `capture/` writes locally only; store-and-forward sync of telemetry to
  the cloud is ticket 1.3/1.4's territory, a genuinely different queue
  mechanism, not built here.
- **No multi-vessel/multi-tenant auth** — one shared secret, one vessel
  spec; real per-vessel auth is ticket 1.4.
- **No deep PGN-variability handling** — `capture/pgn.py` proves the decode
  path against known-good fixtures; real-vessel messiness is ticket 1.3.
- **No IMU hardware integration** — the schema accepts `motion` if present;
  no IMU driver/gateway is built or ordered here (B5: "optional early add,
  not committed").
- **No WebSocket/SSE** — polling only; job durations are in the
  seconds-to-tens-of-seconds sweet spot for a 1–2s poll interval.
- **No rate limiting, no job persistence across restarts, no job
  cancellation.**
- **No Kubernetes/Terraform/managed DB** — one Caddy + one systemd
  service on the cloud VM; SQLite locally; nothing more.
- **No changes to `core/` or `ingest/` source files.**

## Docs

- `ROADMAP.md`: mark B1 done, note it now delivers B5's amended MVP shape
  in full (planner + logging service, one installer, three OS targets).
- `CLAUDE.md`: new gotcha/convention entries for (1) the worker-initializer
  + pool-swap hot-reload pattern (first multiprocessing in this repo), (2)
  the `STINGRAY_ROLE` flag and why cloud/vessel split exactly where it does
  (eccodes packaging pain, LAN-only vs public exposure), (3) the
  `nmea2000`/canboat-database dependency and its PGN-coverage caveat.
- `deploy/README.md`: the actual runbook — per-OS build/install steps, env
  vars, crontab lines, TLS setup.

## Verification

- `pytest -m ""` green (incl. new `test_api_*`/`test_capture_*` files),
  `ruff check .` clean (`api/`/`capture/`/`deploy/` not excluded).
- The manual smoke-test checklist above, run for real at least once before
  considering B1 done — same discipline as tickets 0.5/0.8.
