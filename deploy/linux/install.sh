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

mkdir -p "${INSTALL_DIR}/data"
cp "${SCRIPT_DIR}/../../dist/stingray" "${INSTALL_DIR}/stingray"
chmod +x "${INSTALL_DIR}/stingray"

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
