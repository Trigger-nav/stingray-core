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

## Manual verification checklist (pending, needs real hardware/deployment)

- [x] Real trial build on macOS/arm64 — planner + capture subcommands
  both run correctly (this session).
- [ ] Real Windows and Linux CI-runner builds (`deploy/build-installers.yml`).
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
