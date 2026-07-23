# Ticket D1 — Deploy hardening: contain a crash-looping onefile binary

## Context

Live incident, 2026-07-22: the PyInstaller onefile `stingray` binary
failed to extract one bundled `.so` ("decompression resulted in return
code -1" — PyInstaller's own bootloader error for a corrupted/truncated
archive read). `stingray-planner.service` has `Restart=on-failure` /
`RestartSec=5` and **no restart-limit directives at all**
(`deploy/linux/stingray-planner.service`, confirmed by reading it) —
systemd retried forever, every 5s, and PyInstaller's onefile bootloader
extracts each run's bundled data fresh into a randomly-named
`$TMPDIR/_MEIxxxxxx` directory that only gets cleaned up on a *clean*
exit. A crashed extraction never reaches that cleanup, so each failed
start left a partial extraction behind. After ~960 restarts (this VM's
`/tmp` is tmpfs, confirmed by the incident report), `/tmp` filled —
which then made *every* subsequent start fail too (no room to extract at
all), a self-reinforcing spiral that took the API down until someone
manually cleared `/tmp/_MEI*`.

**Root cause of the original extraction failure is not established**
(plausibly a truncated/corrupt `dist/stingray` from a build that ran
concurrently with a heavy four-pack weather fetch, per the incident
report) — **out of scope**, this ticket is containment and
diagnosability: make this class of failure bounded, visible, and
recoverable without a root cause, not prevent the original trigger.

## Failure chain, and where each fix cuts it

```
extraction fails (any cause)
  -> partial _MEI* dir left behind (no clean-exit cleanup)
  -> systemd restarts unconditionally, every 5s, forever      [FIX 1: bound restarts]
  -> each attempt leaks another _MEI* dir onto tmpfs           [FIX 2: real disk, not tmpfs]
  -> tmpfs eventually fills                                    [FIX 3: periodic cleanup]
  -> every subsequent start now fails too, regardless of the
     original cause (no room to extract at all)                — self-reinforcing, this is
                                                                   the actual outage, not the
                                                                   original single failure
```

Fixes 1–3 each independently break this chain at a different link;
together they mean a repeat of this exact incident tops out at a
handful of leaked directories and a `systemctl status` that says
`failed` in plain English, not an 80-minute invisible spiral.

## Scope, evaluated item by item

### 1. systemd restart backoff — recommended, concrete values proposed

Add to `[Unit]` (not `[Service]` — `StartLimitIntervalSec`/`StartLimitBurst`
are `[Unit]`-scoped in modern systemd, confirmed against Ubuntu 24.04's
systemd version):

```ini
[Unit]
Description=Stingray planner service
After=network.target
StartLimitIntervalSec=30
StartLimitBurst=5
```

**Judgment call, flagged for sign-off**: `StartLimitBurst=5` within
`StartLimitIntervalSec=30` — with the existing `RestartSec=5`, 5
attempts span ~20-25s before the limit trips, so a repeat incident is
now bounded to ~5 leaked directories and a few seconds of restart noise,
not 960 restarts over ~80 minutes. Loose enough that a single genuine
transient failure (e.g. a momentary resource hiccup unrelated to this
incident) still gets a few real retries before giving up.

**The real tradeoff, stated plainly**: once the burst limit trips, the
unit lands in `failed` and **stays there** — systemd does not
auto-retry a failed unit even after the underlying cause clears (e.g.
disk space freed). A human (or an external watchdog, not built here)
must `systemctl reset-failed stingray-planner && systemctl start
stingray-planner`. This is the correct tradeoff (a loud, visible failure
beats an invisible infinite loop that silently degrades the box), but it
means `failed` is only "visible" to someone actually looking —
`deploy/README.md`'s new runbook entry (item 5) is what makes this
greppable/discoverable, not a replacement for real monitoring (named as
a follow-up, not built here).

### 2. TMPDIR on persistent disk, not tmpfs — recommended

PyInstaller's onefile bootloader resolves its extraction root the same
way Python's own `tempfile.gettempdir()` does (checks `TMPDIR` env var
first, POSIX-standard), so this needs **no rebuild** — a systemd-unit-level
`Environment=` line is sufficient:

```ini
[Service]
Environment=TMPDIR=/opt/stingray/tmp
```

`install.sh` gains `mkdir -p "${INSTALL_DIR}/tmp"` alongside its existing
`mkdir -p "${INSTALL_DIR}/data"`.

**Alternative considered, not chosen**: PyInstaller's own `--runtime-tmpdir`
spec-level option bakes the extraction root into the binary at build
time. Rejected in favour of the systemd-unit `TMPDIR=` approach because
it's deploy-time configurable without a rebuild, and it's the more
general fix (also redirects any other stdlib `tempfile` usage the
running process might do, not just PyInstaller's own bootloader
extraction — a grep of `core/`/`api/`/`capture/` found no other
`tempfile` usage today, but this is still the more robust default).

**Tradeoff, stated honestly**: this doesn't eliminate the resource-
exhaustion risk, it moves it — a repeat crash-loop (even bounded by fix
1 to ~5 attempts) now leaks onto `/opt/stingray`'s real disk instead of
memory-backed tmpfs. This is the right trade (disk is typically larger,
more commonly monitored, and doesn't threaten the box's own working
memory the way a full tmpfs can), not a free fix — fix 3 (cleanup) is
what actually bounds the *long-run* accumulation across repeat
incidents, not this item alone.

### 3. Stale `_MEI*` cleanup on service start — recommended, age-bounded not unconditional

```ini
[Service]
ExecStartPre=/usr/bin/find /opt/stingray/tmp -maxdepth 1 -name '_MEI*' -mmin +5 -exec rm -rf {} +
```

**Safety argument**: `ExecStartPre` for a `Type=simple` unit only runs
once systemd has confirmed no instance of *this* unit is currently
active (systemd's own unit state machine serializes `ExecStartPre` before
the next `ExecStart`) — so at the moment this runs, this service's own
extraction dir (if any) is already abandoned, never live. The `-mmin +5`
age bound is deliberate extra margin beyond that guarantee, not
redundant: it protects against the one real edge case systemd's own
lifecycle doesn't cover — a *separate*, manually-invoked instance of the
binary running outside systemd entirely (e.g. an operator testing
`./stingray planner` by hand). A genuinely active extraction directory
is touched/read continuously for as long as its process runs; 5 minutes
is comfortably longer than PyInstaller's own extraction step (seconds)
while still cleaning up same-incident debris promptly. Deleting
*everything* unconditionally was considered and rejected — the age bound
is cheap insurance against exactly the kind of "confident but wrong"
assumption that caused this incident's own spiral in the first place.

### 4. `install.sh` post-install health check — recommended

Mirrors the exact shape of the deploy-finding-#4 lesson
(`CLAUDE.md`'s gotcha: "a deceptively successful install leaving a
broken binary serving") applied to this new failure mode — `systemctl
enable --now`/`restart` reports success the moment the process is
*launched*, not once it's actually healthy; `Type=simple` gives no
stronger guarantee. Add, after the existing start/restart block:

```bash
# Sanity check: the freshly-(re)started service must actually come up
# healthy before this script reports success -- `systemctl enable --now`/
# `restart` only confirm the process was *launched*, not that it didn't
# immediately crash-loop (ticket D1; the same "deceptively successful"
# shape as the 2026-07-13 binary-upgrade finding, applied to extraction
# failures instead of a stale binary).
set +e
HEALTH_OK=false
for _ in $(seq 1 30); do
  if curl -sf -u "${STINGRAY_API_USER}:${STINGRAY_API_PASSWORD}" \
      "http://127.0.0.1:${STINGRAY_PORT:-8000}/v1/health" >/dev/null 2>&1; then
    HEALTH_OK=true
    break
  fi
  sleep 1
done
set -e
if [ "${HEALTH_OK}" != true ]; then
  echo "ERROR: stingray-planner did not report healthy within 30s -- last logs:" >&2
  journalctl -u stingray-planner --no-pager -n 20 >&2
  exit 1
fi
echo "stingray-planner is healthy."
```

Needs `STINGRAY_API_USER`/`STINGRAY_API_PASSWORD`/`STINGRAY_PORT` sourced
from `${INSTALL_DIR}/.env` (a plain `KEY=VALUE` file already, the same
format `EnvironmentFile=` in the systemd unit requires — `source
"${INSTALL_DIR}/.env"` right after the existing `.env`-exists check,
before this block) — every route including `/v1/health` requires Basic
Auth (`api/main.py`'s router-level `Depends(make_auth_dependency(...))`,
confirmed by reading it), so an unauthenticated check isn't an option.
30s budget: real `AppState` startup (vessel spec + geography + weather
npz load, no network calls in the startup path itself) is normally a few
seconds; this leaves real margin without making a healthy install feel
sluggish (typically passes on the 1st-3rd attempt).

### 5. `deploy/README.md` runbook entry — recommended

New subsection (placed after "Upgrading", same "Troubleshooting"-shaped
role deploy-findings' own fixes played out in README already): symptom
(`systemctl status stingray-planner` shows `failed`; `journalctl -u
stingray-planner` shows repeated "decompression resulted in return code
-1" or similar PyInstaller extraction errors), what it means (post-D1:
the restart-limit tripped, not an invisible infinite loop), and the
manual recovery:

```
systemctl status stingray-planner       # confirm it's in `failed`, not just slow
journalctl -u stingray-planner -n 50    # see the actual extraction error
rm -rf /opt/stingray/tmp/_MEI*          # clear any leaked partial extractions
systemctl reset-failed stingray-planner # clear the tripped start-limit
systemctl start stingray-planner
systemctl status stingray-planner       # confirm it's actually up, not just restarted
```

Plus one line noting that if this recurs, the original trigger (D1's own
"not root-caused" scope note) is still worth investigating separately —
this runbook entry treats the symptom, not the cause.

## Out of scope: onedir packaging (named, not built)

A real, more thorough alternative: PyInstaller's `onedir` mode ships a
directory of files the binary runs directly from disk, with **no
runtime extraction step at all** — this entire failure class (extraction
failure, leaked temp dirs, tmpfs exhaustion) would not exist by
construction. Not this ticket's job, named for a future ticket to weigh:
trades the current "one file to scp" deploy convenience for a directory
tree (rsync/tar instead of `scp`+`chmod`), and the Windows NSSM
service-wrapper / cross-platform installer scripts (`deploy/windows/
installer.iss`, `deploy/macos/build_pkg.sh`) would need rework around a
directory rather than a single executable path. Worth doing eventually
if this failure class recurs even after D1's containment, but D1's own
scope is "contain it," not "eliminate the packaging shape that causes
it."

## Verification

This is systemd-unit/bash/markdown work, not `core/`-testable via
`pytest` — verification is real syntax/logic checking plus manual
scenario walkthroughs, matching how the 2026-07-13 deploy-findings work
was verified in this repo:

- `bash -n deploy/linux/install.sh` (syntax check).
- `systemd-analyze verify deploy/linux/stingray-planner.service` if
  available locally (Ubuntu-only tool; note in the plan if unavailable
  on this dev machine and rely on manual review of the unit file syntax
  instead).
- Manual walkthrough of each of the three real scenarios this ticket
  targets: (a) a genuinely broken binary — confirm the unit reaches
  `failed` within the bounded window, not an infinite loop; (b) a
  transient one-off failure — confirm it still recovers within the
  burst budget; (c) a normal, healthy install/upgrade — confirm
  `install.sh`'s new health check passes quickly and doesn't add
  noticeable friction to the common path.
- `git diff --stat core/`: expect **zero** — this ticket touches
  `deploy/linux/stingray-planner.service`, `deploy/linux/install.sh`,
  `deploy/pyinstaller.spec` (comment only, if anything — no runtime-tmpdir
  change per the decision in item 2), and `deploy/README.md` only.

## Acceptance criteria

- `stingray-planner.service` has `StartLimitIntervalSec`/`StartLimitBurst`
  set, plus `Environment=TMPDIR=/opt/stingray/tmp` and the
  age-bounded `ExecStartPre` cleanup.
- `install.sh` creates `${INSTALL_DIR}/tmp`, sources `.env` for
  credentials, and fails loudly (non-zero exit, `journalctl` tail
  printed) if the freshly-(re)started service doesn't report healthy
  within 30s.
- `deploy/README.md` has a greppable symptom + recovery entry.
- `ruff check .` clean (only touches shell/markdown/unit files, but
  run for the record); full `pytest -m ""` green and **unmodified**
  (this ticket makes zero `core/`/`api/` changes, so nothing should
  move); `git diff --stat core/` empty.
- ROADMAP row + CLAUDE.md gotcha, matching every prior deploy-findings
  entry's own level of detail.

## ROADMAP row text (proposed)

> **D1 — Deploy hardening: contain a crash-looping onefile binary** |
> Live incident (2026-07-22): a PyInstaller onefile extraction failure
> ("decompression resulted in return code -1", root cause not
> established) combined with `stingray-planner.service`'s unbounded
> `Restart=on-failure` and tmpfs-backed extraction to spiral into an
> 80-minute, ~960-restart outage — each failed start leaked a partial
> `/tmp/_MEI*` extraction, eventually filling tmpfs, which then
> guaranteed every subsequent start would also fail regardless of the
> original cause. Contained (not root-caused, deliberately) via three
> independent circuit-breakers on the same failure chain:
> `StartLimitIntervalSec=30`/`StartLimitBurst=5` (a repeat incident now
> tops out at ~5 leaked directories and a visible `failed` unit state,
> not an invisible infinite loop); `TMPDIR=/opt/stingray/tmp` (extraction
> moves to persistent disk, no rebuild needed — PyInstaller's bootloader
> already honours `TMPDIR` the same way Python's own `tempfile` module
> does); an age-bounded `ExecStartPre` cleanup (`-mmin +5`, deliberately
> not unconditional — margin against a manually-invoked instance running
> outside systemd, not just relying on systemd's own already-safe unit
> lifecycle). `install.sh` gained a real post-install health check
> (`curl .../v1/health` with a bounded retry, `.env`-sourced credentials
> since every route requires Basic Auth) — the same "deceptively
> successful install" lesson the 2026-07-13 binary-upgrade finding
> taught, applied to a new failure mode. `deploy/README.md` gained a
> greppable symptom + manual-recovery runbook entry. Onedir packaging
> (would eliminate this failure class by construction) named as a real,
> larger follow-up, not built here. Zero `core/`/`api/` diff. |
> Deploy/ops hardening, not application code — `pytest -m ""` green and
> unmodified. |

## CLAUDE.md gotcha entry (proposed, to add on completion)

- A new gotcha recording the full failure chain (extraction failure →
  unbounded restart → tmpfs leak → guaranteed-failure spiral), the three
  independent fixes and why each is layered rather than any one being
  "the" fix, and the real incident numbers (~960 restarts, ~80 minutes)
  as a concrete reference point for anyone tuning
  `StartLimitBurst`/`StartLimitIntervalSec` differently later.

## Implementation order

1. `deploy/linux/stingray-planner.service`: `StartLimitIntervalSec`/
   `StartLimitBurst`, `Environment=TMPDIR=...`, `ExecStartPre` cleanup.
   `systemd-analyze verify` (if available) or manual review.
2. `deploy/linux/install.sh`: `mkdir -p .../tmp`, source `.env`, the
   health-check block. `bash -n` syntax check.
3. `deploy/README.md`: the new runbook subsection.
4. `pytest -m ""` (expected green, unmodified) + `ruff check .` +
   `git diff --stat core/` (expect empty) — the mechanical confirmation
   this ticket touched nothing but `deploy/`.
5. ROADMAP row, CLAUDE.md gotcha.

### Critical files

- `deploy/linux/stingray-planner.service`
- `deploy/linux/install.sh`
- `deploy/README.md`
