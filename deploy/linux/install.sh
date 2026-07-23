#!/usr/bin/env bash
# Linux install script (ticket B1 design 11) -- the Linux-native
# equivalent of a GUI installer: no double-click installer needed for a
# server (cloud VM) or a headless Linux bridge PC. Installs the
# PyInstaller-built `stingray` binary + systemd units. Run as root
# (or via sudo) from the directory containing the built `stingray` binary.
#
# Usage: STINGRAY_ROLE=cloud|vessel ./install.sh
set -euo pipefail

ROLE="${STINGRAY_ROLE:?Set STINGRAY_ROLE=cloud or STINGRAY_ROLE=vessel before running}"
INSTALL_DIR=/opt/stingray
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Stingray (role=${ROLE}) to ${INSTALL_DIR}"

# Captured before touching anything below -- `systemctl enable --now` at
# the end of this script is a no-op on an already-active unit (it doesn't
# restart it), so an *upgrade* of an already-running install needs an
# explicit restart to actually pick up the new binary. Checked this early
# since the unit may not exist yet on a first-ever install (is-active on
# an unknown unit just reports inactive, which is correct here too).
WAS_ACTIVE=false
if systemctl is-active --quiet stingray-planner 2>/dev/null; then
  WAS_ACTIVE=true
fi

mkdir -p "${INSTALL_DIR}/data"
# Ticket D1: PyInstaller onefile extraction root, real disk not tmpfs --
# see stingray-planner.service's own Environment=TMPDIR= comment for why.
mkdir -p "${INSTALL_DIR}/tmp"
# Not a plain `cp` onto ${INSTALL_DIR}/stingray -- found live during a
# real Hetzner binary upgrade (2026-07-13, finding #4): overwriting a
# *running* executable in place fails with "Text file busy" (ETXTBSY) on
# Linux, and under this script's `set -euo pipefail` that aborts the
# whole run right here, leaving the *old* binary still serving --
# deceptively healthy, since the next health check passes against stale
# code. `mv`/`rename(2)` succeeds on a busy file (it only repoints the
# directory entry, not the running process's already-open file
# descriptor) -- the same atomic-replace pattern
# `ingest/grib_common.py`'s `write_npz_atomic` uses for weather npz
# writes. `mktemp` in the same directory as the final path keeps the `mv`
# on one filesystem (a cross-filesystem `mv` falls back to copy+delete,
# which reintroduces the exact busy-file problem this is avoiding).
TMP_BIN="$(mktemp "${INSTALL_DIR}/.stingray.XXXXXX")"
cp "${SCRIPT_DIR}/../../dist/stingray" "${TMP_BIN}"
chmod 0755 "${TMP_BIN}"
mv "${TMP_BIN}" "${INSTALL_DIR}/stingray"

# `api/config.py`'s default data paths (vessel spec, geography, seed
# weather npz) are plain relative paths, resolved against the process's
# *working directory* (systemd's WorkingDirectory=/opt/stingray) -- not
# against deploy/pyinstaller.spec's bundled `datas=` copy, which
# PyInstaller's onefile mode extracts to a throwaway temp dir
# (sys._MEIPASS) at every run, never consulted by a plain open(path) call.
# Found empirically during Hetzner deploy-readiness review: the macOS
# trial build (deploy/README.md's "Verified this session" note) ran
# `dist/stingray` from inside the repo checkout, where `data/` already
# existed in the cwd by coincidence -- masking that install.sh itself
# never actually populates it. Without this copy, stingray-planner
# crash-loops on first start with a FileNotFoundError the moment
# AppState tries to load the vessel spec. Trailing `/.` copies *contents*
# into the already-created DEST dir (idempotent on re-runs) rather than
# nesting a second `data/` inside it, which a bare `cp -r SOURCE DEST`
# would do once DEST already exists.
cp -r "${SCRIPT_DIR}/../../data/." "${INSTALL_DIR}/data/"
echo "Copied data/ (vessel spec, geography, seed weather snapshot) to ${INSTALL_DIR}/data"

# data/weather/*.npz is gitignored -- a checkout that never ran a weather
# fetch (deploy/README.md's Cloud VM runbook, step 4) copies an empty
# weather/ dir here, and the service will crash-loop on FileNotFoundError
# the moment it starts below. Found live during the Hetzner deploy
# (2026-07-13) -- this warns instead of failing silently into a confusing
# systemctl status.
if ! find "${INSTALL_DIR}/data/weather" -name '*.npz' -print -quit 2>/dev/null | grep -q .; then
  echo "WARNING: no *.npz found in ${INSTALL_DIR}/data/weather -- the" >&2
  echo "service will crash-loop until a weather fetch runs (see" >&2
  echo "deploy/README.md's Cloud VM runbook, step 4)." >&2
fi

if [ ! -f "${INSTALL_DIR}/.env" ]; then
  cp "${SCRIPT_DIR}/../.env.example" "${INSTALL_DIR}/.env"
  sed -i "s/^STINGRAY_ROLE=.*/STINGRAY_ROLE=${ROLE}/" "${INSTALL_DIR}/.env"
  echo "Wrote ${INSTALL_DIR}/.env from the template -- edit STINGRAY_API_USER/PASSWORD before starting."
fi

cp "${SCRIPT_DIR}/stingray-planner.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now stingray-planner

# `enable --now` above is a no-op on a unit that's already active -- it
# does not restart it, so a re-run of this script against an already-
# running install (an upgrade) would otherwise leave the old process
# serving even though ${INSTALL_DIR}/stingray now points at the new
# binary (see the WAS_ACTIVE comment near the top). Restart explicitly so
# an upgrade actually takes effect without a separate manual step.
if [ "${WAS_ACTIVE}" = true ]; then
  echo "stingray-planner was already running -- restarting to pick up the new binary"
  systemctl restart stingray-planner
fi

# Ticket D1: `systemctl enable --now`/`restart` above only confirm the
# process was *launched*, not that it didn't immediately crash-loop (the
# same "deceptively successful install" shape as the 2026-07-13 binary-
# upgrade finding, CLAUDE.md -- applied here to onefile extraction
# failures instead of a stale binary). Poll the real health endpoint
# before declaring success.
#
# Credentials are read from .env into a short-lived curl config file
# (`-K`), never passed on the command line -- a `-u user:pass` argument
# is visible to any local user via `ps`, and would also leak into any
# shell trace (`bash -x`) or a future `set -x` added to this script.
# Nothing below echoes the config file's path or contents, or the
# credentials themselves; the only diagnostic printed on failure is
# `journalctl`'s own output, which reflects the service's logs, not this
# script's variables.
# shellcheck source=/dev/null
source "${INSTALL_DIR}/.env"

CURL_CFG="$(mktemp)"
chmod 600 "${CURL_CFG}"
trap 'rm -f "${CURL_CFG}"' EXIT
printf 'user = "%s:%s"\n' "${STINGRAY_API_USER}" "${STINGRAY_API_PASSWORD}" > "${CURL_CFG}"

set +e
HEALTH_OK=false
for _ in $(seq 1 30); do
  if curl -sf -K "${CURL_CFG}" "http://127.0.0.1:${STINGRAY_PORT:-8000}/v1/health" >/dev/null 2>&1; then
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

if [ "${ROLE}" = "vessel" ]; then
  cp "${SCRIPT_DIR}/stingray-capture.service" /etc/systemd/system/
  systemctl daemon-reload
  echo "stingray-capture installed but NOT started -- edit its ExecStart"
  echo "gateway flags for this vessel's actual hardware first, then run:"
  echo "  systemctl enable --now stingray-capture"
else
  echo "role=cloud: stingray-capture not installed (no NMEA bus on a cloud VM)."
  echo "Set up the scheduled GRIB fetch separately -- see crontab.example"
  echo "and CLAUDE.md's cfgrib/eccodes gotcha (needs the ingest extras,"
  echo "not bundled into this binary)."
fi

echo "Done. Reverse-proxy TLS (Caddy) is not set up by this script -- see deploy/README.md."
