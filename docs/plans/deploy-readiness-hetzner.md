# Deploy readiness review — Hetzner CPX21 / Ubuntu 24.04 (2026-07-09)

## Context

Ticket B1 built the deploy tooling (`deploy/`) and verified it with one
real PyInstaller trial build — macOS/arm64, run from inside the repo
checkout. B2's manual real-browser verification is now done. The next
concrete step is a real cloud-role deploy: a Hetzner CPX21, Ubuntu 24.04,
this week, to host the planner API the GitHub-Pages demo (`prototype/`)
points at. This is a review of `deploy/linux/install.sh`, the systemd
units, `deploy/linux/Caddyfile`, `crontab.example`, and `deploy/.env.example`
against that fresh target — specifically hunting for assumptions that only
held because the only real trial build so far was macOS, run from a repo
checkout, not a genuinely fresh box via the actual install path.

## Finding 1 (real bug, fixed): `install.sh` never copied `data/`

`api/config.py`'s default data paths (`STINGRAY_VESSEL_SPEC_PATH`,
`STINGRAY_WEATHER_NPZ_PATH`, the geography paths) are plain relative
strings — `"data/vessel_specs/mys_50m_default.yaml"` etc. — resolved by
`core/vessel_spec.py`/`core/geography.py`'s plain `open(path)` calls
against the process's *working directory*. `stingray-planner.service` sets
`WorkingDirectory=/opt/stingray`. `install.sh` copied only the built
binary and wrote `.env` — `mkdir -p "${INSTALL_DIR}/data"` created an
*empty* directory, never populated.

Why this wasn't caught by the macOS trial build: that build ran
`dist/stingray planner` from inside the repo checkout (confirmed by
re-reading the session's own verification notes), where `data/` already
existed in the cwd by coincidence. The actual `install.sh` → `/opt/stingray`
path was never exercised end to end.

`deploy/pyinstaller.spec`'s `datas=[(repo_root/"data", "data")]` looks
like it should cover this, but doesn't: PyInstaller's onefile mode
extracts bundled `datas` to a throwaway temp directory (`sys._MEIPASS`)
at every process start, and nothing in `core/`/`api/` checks
`sys._MEIPASS` — confirmed by grep, zero hits for `_MEIPASS`/`sys.frozen`
anywhere in the repo. The bundled copy is currently inert dead weight,
never actually read.

**Decision: fix `install.sh` to copy `data/` directly, leave the
PyInstaller bundling as-is.** Two ways to close this gap: make `core/`
`sys._MEIPASS`-aware (so the binary is self-contained), or have
`install.sh` copy the real `data/` from the checkout it's already being
run from (the build step already requires a full checkout on the target
OS, since PyInstaller can't cross-compile — the checkout is right there).
Chose the second: minimal, doesn't touch `core/` (which CLAUDE.md
establishes as deployment-agnostic by design), and doesn't risk the
already-trial-verified PyInstaller spec right before a real deploy. The
`datas=` bundling staying in as unused dead weight is a known, accepted
inefficiency (slightly larger binary), not fixed here — flagged, not
silently left for someone to rediscover confused later.

Implementation: `cp -r "${SCRIPT_DIR}/../../data/." "${INSTALL_DIR}/data/"`
— the trailing `/.` copies *contents* into the already-`mkdir -p`'d
destination, tested locally for idempotency (a second run doesn't nest a
second `data/` inside itself, which a bare `cp -r SOURCE DEST` would do
once DEST already exists).

## Finding 2 (real gap, fixed): `.env.example` missing `STINGRAY_CORS_ORIGINS`

The single most likely real-deploy failure mode: a cloud instance comes
up healthy (`/v1/health` responds fine to `curl`), but the hosted demo
never shows a plan, with **no server-side error to grep for** — a
CORS-rejected preflight never reaches application code at all, it's
answered by the browser itself. `.env.example` didn't mention
`STINGRAY_CORS_ORIGINS` at all (silently falls back to the
`http://localhost:8080` dev default, which never matches a real GitHub
Pages origin). Added, with the exact origin the demo is actually served
from (`https://trigger-nav.github.io`, per CLAUDE.md) as the example
value, and an explicit "this is the #1 silent-failure risk" framing.

Also added (previously present in `api/config.py` but undocumented in the
template): the geography-path overrides and the job-queue tuning knobs
(`STINGRAY_MAX_QUEUE_DEPTH`/`STINGRAY_JOB_TTL_S`/etc.) — commented out
with their defaults shown, not because they need changing for this
deploy, but so a future reader doesn't have to go read `api/config.py` to
discover they exist.

## Finding 3 (real uncertainty, documented not resolved): `eccodes` on Ubuntu 24.04

CLAUDE.md's ticket 0.5 gotcha confirms `cfgrib`/`eccodes` live-verified on
macOS (`brew install eccodes`) — there is no equivalent live verification
for Ubuntu's `libeccodes-dev` apt package's version against this
project's `cfgrib` pin. `libeccodes-dev` is the standard, long-shipped
Ubuntu package and is very likely fine, but "very likely" isn't the same
as verified, and this is exactly the kind of gap this review exists to
surface rather than paper over.

**Decision: document the standard path, but make the runbook's step 6 a
smoke test *before* trusting cron with it** (`import cfgrib` + one real
`fetch_grib_ecmwf` invocation), with the documented fallback (`pip install
eccodes` alone, which for common Linux platforms can vendor a compiled
ecCodes via wheel, no system package) noted but not pre-verified either.
Neither path is asserted to definitely work — the runbook is written so
the operator finds out immediately, from one command, rather than
discovering it days later when cron silently fails.

## Finding 4 (operational decision): Caddy as a managed systemd service, not `caddy run`

`deploy/linux/Caddyfile`'s own comment says `caddy run --config Caddyfile`
— a manual, foreground invocation, fine for a quick test but wrong for a
real always-on deployment (doesn't survive reboots, no systemd log
integration, no automatic restart). **Decision: install Caddy via its
official apt repository** (the standard, widely-documented method — adds
Caddy's own apt source + GPG key, `apt install caddy`), which registers
`caddy.service` and reads `/etc/caddy/Caddyfile` natively. The runbook
copies the repo's `Caddyfile` (with the placeholder domain substituted)
into `/etc/caddy/Caddyfile` and reloads the managed service, rather than
running Caddy by hand. `deploy/linux/Caddyfile`'s own inline comment
wasn't corrected in place (it's still a reasonable quick-test instruction
for someone who wants to try Caddy manually first) — the runbook is the
canonical real-deployment path, layered on top.

## Deliberately not addressed this review

- **No dedicated service user.** `stingray-planner.service`/
  `stingray-capture.service` have no `User=`/`Group=`, and `install.sh`
  says "run as root." Matches B1's own "cheap single VM, not IaC-scale"
  framing, and isn't specific to Ubuntu 24.04 (the same gap exists on the
  already-trial-verified macOS build) — a real, known hardening gap,
  flagged here rather than fixed under this week's deploy deadline. A
  reasonable follow-up (create a `stingray` system user, `chown -R` `/opt/
  stingray`, add `User=stingray` to both unit files) once the box is
  stable, not before.
- **No automated end-to-end test of the runbook itself.** It's reviewed
  against a careful fresh-box mental model and the one concrete bug that
  model surfaced (Finding 1) but not yet run against a real box — that's
  the actual Hetzner deploy this week. `deploy/README.md`'s manual
  verification checklist has a dedicated pending item for it.
- **Real Windows/Linux CI-runner builds** (`deploy/build-installers.yml`)
  remain unrun — out of scope for a single cloud-role deploy, tracked
  separately (pre-existing checklist item).

## Verification

- `bash -n deploy/linux/install.sh` — syntax check, passes.
- The `cp -r SOURCE/. DEST/` idempotency claim tested directly (a
  throwaway local dir, two runs, confirmed no nesting on the second).
- The runbook itself is the deliverable of this review — real end-to-end
  verification is the actual Hetzner deploy, not yet done as of this
  writing.
