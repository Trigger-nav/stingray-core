# Stingray deployment (ticket B1/B5)

One artefact (`deploy/pyinstaller.spec` -> the `stingray` binary,
`stingray planner`/`stingray capture` subcommands), three OS targets.
Same binary, same code, on a cloud VM or a yacht's bridge PC — only the
OS-native service-registration wrapper differs (contract point 2).

## Building

PyInstaller cannot cross-compile — build separately, on each target OS:

```
pip install -e ".[api,capture]" pyinstaller
pyinstaller deploy/pyinstaller.spec --distpath dist --workpath build
```

Produces `dist/stingray` (`dist/stingray.exe` on Windows). CI does this
across a 3-OS matrix — copy `deploy/build-installers.yml` to
`.github/workflows/build-installers.yml` to activate it (kept out of
`.github/workflows/` by default, matching `prototype/deploy/
github-pages.yml`'s existing template-not-active precedent).

**Verified this session** (a real trial build, on macOS/arm64 — not
assumed to work from the spec file alone): the planner subcommand serves
real HTTP requests and runs a real `optimise()` job through a real
`ProcessPoolExecutor`; the capture subcommand's `--help` and PGN-decode
dependency both load correctly. Two real, empirically-found packaging
gaps are already fixed in `deploy/pyinstaller.spec`/`deploy/
stingray_cli.py` (see their comments) — `nmea2000`'s
`decoder_formats`/`encoder_formats` need explicit `hiddenimports`, and
`multiprocessing.freeze_support()` must run before CLI-argument dispatch
or the planner's worker pool comes up broken. **Not yet verified**: an
actual Windows or macOS CI-runner build (only macOS/arm64, run locally
in this session, has been tried for real) — budget a real run of
`deploy/build-installers.yml` before trusting the Windows/Linux builds.

## Configuring

Copy `deploy/.env.example` to `.env` next to the binary and edit it —
every service wrapper below (systemd/launchd/NSSM) loads it uniformly via
`deploy/stingray_cli.py`'s `python-dotenv` call at startup (found the
`.env`-in-working-directory approach necessary during implementation: the
three OS's native service-environment mechanisms differ enough — systemd
`EnvironmentFile=`, launchd's static plist dict, NSSM's per-key `set` —
that a single file loaded by the binary itself is simpler than three
different injection mechanisms).

`STINGRAY_ROLE=cloud` or `STINGRAY_ROLE=vessel` is the one setting that
matters most (api/config.py) — see CLAUDE.md's gotcha for what it changes.

## Local dev server (no packaging)

For local work against a real API instance -- e.g. ticket B2's manual
demo-UI verification -- skip the PyInstaller build entirely:

```
pip install -e ".[api]"
STINGRAY_API_USER=... STINGRAY_API_PASSWORD=... STINGRAY_CORS_ORIGINS=http://localhost:8080 \
  python3 -m api.main
```

(or `uvicorn api.main:app --port 8000` directly, equivalent). Serve
`prototype/` on a *different* local port (`python3 -m http.server 8080`
from that directory) so the demo UI actually exercises CORS rather than
being accidentally same-origin.

**Orphaned dev servers, found for real during B2's verification:** both
of the above are long-running foreground processes with no packaged
service wrapper around them (that's the whole point of this section) --
background them (`&`/`nohup`) for a multi-step manual test and it's easy
to lose track and leave several accumulating across a session, each still
holding its port. Symptom: a fresh `python3 -m api.main` mysteriously
fails to bind, or requests silently hit a *stale* process serving old
code. Find and clear them before assuming a bug:

```
lsof -nP -iTCP:8000 -sTCP:LISTEN   # planner API
lsof -nP -iTCP:8080 -sTCP:LISTEN   # demo UI static server
kill <pid>                         # from lsof's PID column, once confirmed stale
```

(`-n` skips hostname resolution, `-P` skips port-name resolution —
without them lsof prints service names like `irdmi`/`http-alt` instead of
`8000`/`8080`, which reads as "some unrelated service" and costs a double-
take before you realize it's your own stale process.)

## Installing

### Linux (cloud VM)

```
sudo STINGRAY_ROLE=cloud ./deploy/linux/install.sh
```

Installs `stingray-planner.service` (always) and `stingray-capture.service`
(vessel role only, not auto-started — there's no NMEA bus on a cloud VM).
Add `deploy/linux/crontab.example`'s lines to `crontab -e` for the
scheduled GRIB fetch (needs the `ingest` extras — cfgrib/eccodes —
installed separately; **not** bundled into the `stingray` binary, see
`deploy/pyinstaller.spec`'s `excludes`). Put `deploy/linux/Caddyfile`
in front for TLS (public-internet-facing — required, see design 9).

**For a real deployment, use the full "Cloud VM runbook" section below**
instead of this quick reference — it covers the base-package/build/DNS/
firewall steps around `install.sh` this section skips, plus two fixes for
real gaps found during the Hetzner deploy-readiness review and the first
real Hetzner deploy: `install.sh` never copied `data/` (see that
section's step 5), so a fresh box outside a repo checkout would
crash-loop on first start; and weather must be fetched *before*
`install.sh` runs (step 4), or the same crash-loop happens anyway on a
box that otherwise has no weather file yet.

### macOS (bridge PC)

```
./deploy/macos/build_pkg.sh   # run where dist/stingray (macOS build) already exists
```

Produces `dist/stingray-installer.pkg` — installs to `/usr/local/stingray`,
registers both `com.stingray.planner`/`com.stingray.capture` as
`LaunchDaemon`s (not `LaunchAgent`s — runs at boot regardless of user
login, matching B5's "always-on logging"). Edit
`/Library/LaunchDaemons/com.stingray.capture.plist`'s `--gateway`/`--host`/
`--device` flags for this vessel's actual hardware during commissioning,
then `sudo launchctl load -w /Library/LaunchDaemons/com.stingray.capture.plist`.

### Windows (bridge PC)

Compile `deploy/windows/installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php)
(needs `dist/stingray.exe` already built, and
[NSSM](https://nssm.cc)'s `win64/nssm.exe` placed next to the `.iss`
file first — not built by this repo). Registers `StingrayPlanner`
(started) and `StingrayCapture` (installed, not started — edit its
`--gateway` flags for this vessel's hardware first, then
`nssm start StingrayCapture`) as Windows services.

**Service-wrapper decision** (made during the real trial build above, per
direct instruction, not deferred): NSSM, not `pywin32`. The trial build
confirmed `stingray_cli.py` needs zero Windows-service-specific code once
`multiprocessing.freeze_support()` is in place — NSSM wraps the existing,
unmodified binary as a service via external configuration, with no
`pywin32` dependency to bundle (a Windows-only addition, another
potential PyInstaller hidden-import gap, untestable outside a real
Windows CI runner) and no service-control-manager-aware code inside the
cross-platform entry point.

## Cloud VM runbook (Ubuntu 24.04, e.g. Hetzner CPX21)

A from-scratch, copy-paste sequence for standing up the `cloud` role on a
fresh Ubuntu 24.04 VM. Written for someone comfortable on the command line
but not a Linux daily driver — every step says what it's for, not just
what to type. Replace `YOUR_DOMAIN` (e.g. `stingray-api.example.com`) and
`YOUR_SERVER_IP` throughout.

**Before you start:** point `YOUR_DOMAIN`'s DNS A record at `YOUR_SERVER_IP`
now — step 8's TLS cert issuance needs that to have propagated, and DNS
can take a few minutes to an hour.

### 1. Base packages

```
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip build-essential git curl
python3 --version   # expect 3.12.x -- Ubuntu 24.04 ships it by default, already >=3.11
```

No `deadsnakes`/pyenv needed — this is specifically why 24.04 (not 22.04,
which ships 3.10) was the right choice here.

### 2. Get the code

```
git clone <your Stingray repo URL> stingray && cd stingray
```

(Or `scp`/`rsync` a checkout over if the repo isn't push-accessible from
the VM. Either way, the full checkout — not just `dist/` — needs to be on
the box: the build step below runs `pyinstaller` here, and `install.sh`
copies `data/` from this checkout, not from anywhere else.)

### 3. Build the binary

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[api,capture]" pyinstaller
pyinstaller deploy/pyinstaller.spec --distpath dist --workpath build
ls dist/stingray   # should exist now
```

Building on the target OS is required (PyInstaller can't cross-compile) —
this *is* the Linux build, there's no separate step. Both `api` and
`capture` extras are installed even for a cloud-only box: it's one binary
with both subcommands (design 11); the unused `capture` half is harmless
dead weight here, not a problem to route around.

### 4. Weather fetch (before install — avoids a fresh-box crash-loop)

**Do this before step 5, not after.** `data/weather/*.npz` is gitignored,
so this checkout has no weather file yet; `install.sh` only copies
whatever's already in `data/`. Fetching first means the file rides along
in that copy, and `stingray-planner.service` has real weather the very
first time it starts — the original version of this runbook fetched
weather *after* install/start and crash-looped on a fresh box until
someone ran a fetch by hand (found live on the first real Hetzner
deploy, 2026-07-13; see CLAUDE.md's gotcha).

The `ingest` extras (cfgrib, needing the system `eccodes` C library) are
deliberately **not** bundled into the `stingray` binary (kept out of the
Windows/macOS builds, CLAUDE.md's gotcha) — cloud-role weather ingest runs
as a separate, plain Python invocation, in its own venv (the same venv
step 7 points cron at later):

```
sudo apt install -y libeccodes-dev libeccodes-tools
sudo python3 -m venv /opt/stingray-ingest/venv
sudo /opt/stingray-ingest/venv/bin/pip install -e ".[ingest]"
# (run from the repo checkout, e.g. /home/<you>/stingray)
```

**Verified live on Ubuntu 24.04 (2026-07-13 Hetzner deploy)** — the
standard `libeccodes-dev` apt package works, closing what was previously
an open question (only macOS's `brew install eccodes` had been confirmed
live before, ticket 0.5). Still worth running this smoke test on any new
box before trusting cron with it, rather than assuming:

```
sudo /opt/stingray-ingest/venv/bin/python3 -c "import cfgrib; print('cfgrib OK')"
cd /home/<you>/stingray && sudo /opt/stingray-ingest/venv/bin/python3 -m ingest.fetch_grib_ecmwf
ls data/weather/ecmwf_western_med.npz   # should exist now
```

Note **no `--out`** this time (unlike the cron lines in step 7) — this
writes to the default `data/weather/ecmwf_western_med.npz`, *inside the
checkout*, so step 5's `install.sh` copies it into `/opt/stingray/data/`
along with everything else. If it produces a fresh `.npz` without error,
the ingest path works end-to-end. If `pip install`/`import cfgrib` fails
looking for `eccodes` symbols, `libeccodes-dev`'s packaged version is too
old for this project's `cfgrib` pin — the documented fallback is `pip
install eccodes` alone (the ECMWF Python package, which for common Linux
platforms can vendor its own compiled ecCodes via a wheel, no system
package needed) in place of the apt step; not pre-verified here either,
try the apt route first since it's the standard documented path.

### 5. Install

```
sudo STINGRAY_ROLE=cloud ./deploy/linux/install.sh
```

Copies the binary and `data/` (vessel spec, geography, and now the seed
weather npz fetched in step 4) to `/opt/stingray`, writes
`/opt/stingray/.env` from the template, installs and starts
`stingray-planner.service`. It will be running against placeholder
credentials at this point — that's expected, fixed next. `install.sh`
itself warns (doesn't block) if `data/weather/` still ends up empty —
that means step 4 was skipped or failed.

### 6. Configure `.env`

```
sudo nano /opt/stingray/.env
```

At minimum, set:

```
STINGRAY_API_USER=<a real username>
STINGRAY_API_PASSWORD=<a real, generated password>
STINGRAY_CORS_ORIGINS=https://trigger-nav.github.io
```

The CORS origin must be the exact origin the hosted demo is served from
(scheme+host, no path) — get this wrong and requests fail silently in the
browser with no server-side log line to grep for, since a CORS-rejected
preflight never reaches application code at all. Then:

```
sudo systemctl restart stingray-planner
sudo systemctl status stingray-planner   # should show "active (running)"
curl -u <user>:<password> http://127.0.0.1:8000/v1/health
```

The `curl` should return a JSON health payload. If it 401s, the
credentials in `.env` and the ones you're `curl`ing with don't match. If
the service isn't running at all, `journalctl -u stingray-planner -n 50
--no-pager` is the first thing to check — a "no weather file at ..."
message there (not a bare traceback, as of the 2026-07-13 Hetzner deploy
fix) means step 4's fetch didn't happen before this step ran; run it,
`cp -r data/weather/. /opt/stingray/data/weather/`, and restart.

### 7. Scheduled weather fetch (cron)

Once step 4's manual fetch has confirmed the ingest path works, install
the recurring schedule so weather stays current without anyone logging
in — this needs `/opt/stingray` to already exist (step 5), since these
lines write straight there:

```
sudo crontab -e
# paste the two lines from deploy/linux/crontab.example, editing the
# venv path to /opt/stingray-ingest/venv if you used a different one
```

Cadences match the real, confirmed-live cadences from ticket 0.5: NOMADS
hourly-ish, ECMWF 3-hourly, each with its own confirmed publication delay
and valid-cycle set (`ingest/grib_common.py`'s `latest_available_cycle_utc`
— NOMADS 00/06/12/18z at ~5h delay, ECMWF 00/12z only at ~9h delay; the
first Hetzner deploy picked ECMWF's not-yet-published 12z under NOMADS'
looser assumptions before this was fixed). Both fetchers now also
self-heal past a temporary "cycle not yet published" 404 by falling back
to the previous cycle, logging each fallback step — check
`/var/log/stingray-fetch-*.log` if you ever want to confirm one fired.

**Multi-pack deployments (ticket R1):** the two crontab lines above are
the single-pack (Med-only) form -- correct for every deployment today.
Once `STINGRAY_REGION_PACKS_PATH` is set (`docs/region-pack-runbook.md`),
replace them with `ingest.fetch_all_packs`, a thin wrapper that loops the
same two fetchers over every pack listed in that manifest instead of one
hardcoded bbox:

```
0 * * * *   cd /opt/stingray-ingest && /opt/stingray-ingest/venv/bin/python3 -m ingest.fetch_all_packs --packs-manifest /opt/stingray/data/region_packs.yaml --source nomads >> /var/log/stingray-fetch-nomads.log 2>&1
5 */3 * * * cd /opt/stingray-ingest && /opt/stingray-ingest/venv/bin/python3 -m ingest.fetch_all_packs --packs-manifest /opt/stingray/data/region_packs.yaml --source ecmwf >> /var/log/stingray-fetch-ecmwf.log 2>&1
```

The packs manifest is cron's single source of truth for which bboxes get
fetched -- add a pack to `region_packs.yaml` and both cron lines pick it
up automatically, no crontab edit needed. `GET /v1/weather/latest.npz`
gains a matching `?pack=<id>` query param (default `"med"`) for the
vessel-role pull side (`api/weather_sync.py`, which itself only ever
pulls the pack(s) *that vessel's own* `region_packs.yaml` configures it
to care about, not every pack the cloud role happens to serve).

### 8. TLS (Caddy)

```
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

This installs Caddy as its own systemd service (`caddy.service`), reading
`/etc/caddy/Caddyfile` — don't use `deploy/linux/Caddyfile`'s own comment
(`caddy run --config Caddyfile`, a manual/foreground invocation) for a
real deployment; install it as the managed service instead:

```
sudo sed "s/your-cloud-instance.example.com/YOUR_DOMAIN/" deploy/linux/Caddyfile | sudo tee /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy requests a Let's Encrypt cert automatically on first request to
`YOUR_DOMAIN` over port 80/443 — this is why DNS needed to already be
pointed at `YOUR_SERVER_IP` (the top of this runbook). Confirm:

```
curl -u <user>:<password> https://YOUR_DOMAIN/v1/health
```

over real HTTPS, through Caddy, not `127.0.0.1:8000` directly.

### 9. Firewall

Confirm ports 80 and 443 are reachable from the public internet (Caddy
needs them for the ACME challenge and to actually serve traffic) and port
8000 is **not** — the planner only needs to be reachable via Caddy's
reverse proxy on `127.0.0.1:8000`, never directly:

```
sudo ufw status   # if ufw is active: allow 22,80,443/tcp, nothing else
```

(On Hetzner, also check the Cloud Firewall attached to the server in
their web console — a `ufw` rule alone doesn't help if their edge
firewall blocks the port first.)

### 10. Point the demo at this instance

`prototype/stingray_planner.html`'s `API_BASE` constant (top of the
`id="shared"` script block) still says `http://localhost:8000` — update
it to `https://YOUR_DOMAIN`, commit, and let the demo's deploy workflow
redeploy (`prototype/deploy/HOSTING.md` has the full mixed-content
reasoning for why this must be the HTTPS URL, not HTTP, once the demo
itself is served over HTTPS via GitHub Pages).

### 11. End-to-end smoke test

The same sequence used to verify ticket B2 locally, now against the real
deployment:

```
AUTH="<user>:<password>"
curl -s -u $AUTH https://YOUR_DOMAIN/v1/health
curl -s -u $AUTH https://YOUR_DOMAIN/v1/vessel
curl -s -u $AUTH "https://YOUR_DOMAIN/v1/weather/field?h=3" | head -c 200; echo

JOB=$(curl -s -u $AUTH -X POST https://YOUR_DOMAIN/v1/plans \
  -H "Content-Type: application/json" \
  -d '{"pace": 70, "comfort": 30, "latest_arrival_h": null}' | python3 -c "import json,sys;print(json.load(sys.stdin)['job_id'])")
echo "job: $JOB — polling…"
for i in $(seq 1 40); do
  R=$(curl -s -u $AUTH https://YOUR_DOMAIN/v1/plans/$JOB)
  ST=$(echo "$R" | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])")
  echo "poll $i: $ST"
  [ "$ST" = "done" ] || [ "$ST" = "failed" ] && { echo "$R"; break; }
  sleep 1.5
done
```

A `done` status with real `candidates` confirms the whole chain — Caddy
TLS, auth, the planner service, real geography+weather — is working. Then
open the hosted demo in a real browser and confirm a plan actually
renders (the one verification step this runbook can't script for you).

### Upgrading

Once a box is already running (steps 1–11 done once), a code update is
the same checkout, rebuilt and reinstalled in place — no manual
stop/start dance needed (found live during a follow-up binary upgrade,
2026-07-13, finding #4: `install.sh` used to `cp` straight onto the
running binary, which fails with "Text file busy" and, under the
script's `set -euo pipefail`, aborted mid-run with the *old* binary still
serving — deceptively healthy, since the next health check passes on
stale code. Fixed via copy-to-temp-then-`mv`, same atomic-replace pattern
as `ingest/grib_common.py`'s `write_npz_atomic`; `install.sh` now also
restarts `stingray-planner` itself at the end if it was already running):

```
cd stingray && git pull
source .venv/bin/activate
pyinstaller deploy/pyinstaller.spec --distpath dist --workpath build
sudo STINGRAY_ROLE=cloud ./deploy/linux/install.sh
```

Verify the upgrade actually took — a stale binary `mtime` after a
supposedly successful `install.sh` run means the copy failed silently
somewhere upstream (re-check the `pyinstaller` output above it):

```
stat -c '%y' /opt/stingray/stingray   # should be ~now
curl -u <user>:<password> http://127.0.0.1:8000/v1/health
```

## Manual verification checklist (pending, needs real hardware/deployment)

- [x] Real trial build on macOS/arm64 — planner + capture subcommands
  both run correctly (this session).
- [ ] Real Windows and Linux CI-runner builds (`deploy/build-installers.yml`)
  — the Linux **target-box** build (below) is now verified; the CI-matrix
  build specifically is still untried.
- [x] **Real Ubuntu 24.04 cloud deploy (Hetzner CPX21) — done 2026-07-13.**
  `api.stingraymarinetechnology.com` served over Caddy TLS, cron
  installed, the hosted demo running end-to-end against it. Verified live
  this deploy: the Linux PyInstaller build on the actual target box;
  `install.sh` including the `data/` copy fix; Ubuntu 24.04's
  `libeccodes-dev` apt route (cfgrib imports and fetches cleanly — the
  previously-flagged unknown is closed). Found and fixed three real
  defects in the process — see `docs/plans/deploy-findings-2026-07-13.md`
  and `docs/plans/deploy-findings-2026-07-13-fixes.md` — before trusting
  cron unattended: a runbook-ordering fresh-box crash-loop, wrong
  per-source cycle-publication assumptions for ECMWF, and no fallback on
  a missing cycle.
- [ ] Real gateway smoke test: one real YDEN-02 and/or Actisense unit,
  `stingray capture` logs real frames to SQLite (`capture/gateway.py`'s
  protocol parsing is verified against cited real-world examples and
  canboat's reference implementation, not yet against physical hardware).
- [ ] Real weather-sync smoke test: a cloud-role instance fetching live,
  a vessel-role instance pulling from it, hot-swap firing end-to-end
  (this deploy verified the cloud-role cron fetch and demo-facing side;
  a real vessel-role instance pulling from it is still untried).
- [x] Real TLS check: Caddy obtaining a real Let's Encrypt cert against a
  real public domain — done 2026-07-13 (`api.stingraymarinetechnology.com`).
- [x] Human sanity check: HTTP Basic Auth prompt appears and correctly
  gates access in a real browser — implied by the hosted demo running
  end-to-end against the real deployment this deploy (the demo only ever
  renders a plan if the browser successfully authenticated against the
  Basic-Auth-gated API).
