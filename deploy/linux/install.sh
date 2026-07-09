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
