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
firewall steps around `install.sh` this section skips, plus a fix (see
that section's step 4) for a real gap `install.sh` had until the Hetzner
deploy-readiness review: it never copied `data/`, so a fresh box outside
a repo checkout would crash-loop on first start.

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

### 4. Install

```
sudo STINGRAY_ROLE=cloud ./deploy/linux/install.sh
```

Copies the binary and `data/` (vessel spec, geography, a seed weather
snapshot) to `/opt/stingray`, writes `/opt/stingray/.env` from the
template, installs and starts `stingray-planner.service`. It will be
running against placeholder credentials at this point — that's expected,
fixed next.

### 5. Configure `.env`

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
--no-pager` is the first thing to check — a `FileNotFoundError` there
means step 4's `data/` copy didn't happen (confirm you're running
`install.sh` from a full repo checkout, not just `dist/`).

### 6. Scheduled weather fetch (cloud role only)

The `ingest` extras (cfgrib, needing the system `eccodes` C library) are
deliberately **not** bundled into the `stingray` binary (kept out of the
Windows/macOS builds, CLAUDE.md's gotcha) — cloud-role weather ingest runs
as a separate, plain Python invocation via cron, in its own venv:

```
sudo apt install -y libeccodes-dev libeccodes-tools
sudo python3 -m venv /opt/stingray-ingest/venv
sudo /opt/stingray-ingest/venv/bin/pip install -e ".[ingest]"
# (run from the repo checkout, e.g. /home/<you>/stingray)
```

**Not yet verified on Ubuntu 24.04 specifically** (only macOS's `brew
install eccodes` has been confirmed live, ticket 0.5) — `libeccodes-dev`
is Ubuntu's standard ecCodes package and has shipped since well before
24.04, but confirm it before trusting cron with it:

```
sudo /opt/stingray-ingest/venv/bin/python3 -c "import cfgrib; print('cfgrib OK')"
cd /home/<you>/stingray && sudo /opt/stingray-ingest/venv/bin/python3 -m ingest.fetch_grib_ecmwf --out /opt/stingray/data/weather/ecmwf_western_med.npz
```

If that second command produces a fresh `.npz` without error, the ingest
path works end-to-end. If `pip install`/`import cfgrib` fails looking for
`eccodes` symbols, `libeccodes-dev`'s packaged version is too old for this
project's `cfgrib` pin — the documented fallback is `pip install
eccodes` alone (the ECMWF Python package, which for common Linux
platforms can vendor its own compiled ecCodes via a wheel, no system
package needed) in place of the apt step; not pre-verified here either,
try the apt route first since it's the standard documented path.

Once the smoke test above passes, install the actual schedule:

```
sudo crontab -e
# paste the two lines from deploy/linux/crontab.example, editing the
# venv path to /opt/stingray-ingest/venv if you used a different one
```

### 7. TLS (Caddy)

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

### 8. Firewall

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

### 9. Point the demo at this instance

`prototype/stingray_planner.html`'s `API_BASE` constant (top of the
`id="shared"` script block) still says `http://localhost:8000` — update
it to `https://YOUR_DOMAIN`, commit, and let the demo's deploy workflow
redeploy (`prototype/deploy/HOSTING.md` has the full mixed-content
reasoning for why this must be the HTTPS URL, not HTTP, once the demo
itself is served over HTTPS via GitHub Pages).

### 10. End-to-end smoke test

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

## Manual verification checklist (pending, needs real hardware/deployment)

- [x] Real trial build on macOS/arm64 — planner + capture subcommands
  both run correctly (this session).
- [ ] Real Windows and Linux CI-runner builds (`deploy/build-installers.yml`).
- [ ] Real Ubuntu 24.04 cloud deploy (Hetzner CPX21, planned this week) —
  the "Cloud VM runbook" section above is written and reviewed against a
  fresh-box mental model (and fixed one real gap it found: `install.sh`
  wasn't copying `data/`), but not yet run against a real box end to end.
  The one specifically flagged unknown: whether Ubuntu 24.04's
  `libeccodes-dev` apt package is new enough for this project's `cfgrib`
  pin (step 6's smoke test is the thing to watch).
- [ ] Real gateway smoke test: one real YDEN-02 and/or Actisense unit,
  `stingray capture` logs real frames to SQLite (`capture/gateway.py`'s
  protocol parsing is verified against cited real-world examples and
  canboat's reference implementation, not yet against physical hardware).
- [ ] Real weather-sync smoke test: a cloud-role instance fetching live,
  a vessel-role instance pulling from it, hot-swap firing end-to-end.
- [ ] Real TLS check: Caddy obtaining a real Let's Encrypt cert against a
  real public domain.
- [ ] Human sanity check: HTTP Basic Auth prompt appears and correctly
  gates access in a real browser before calling any demo ready.
