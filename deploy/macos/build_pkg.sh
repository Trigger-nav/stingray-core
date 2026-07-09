#!/usr/bin/env bash
# macOS installer build (ticket B1 design 11): wraps the PyInstaller-built
# `stingray` binary + LaunchDaemon plists into a `.pkg` via `pkgbuild`/
# `productbuild` -- run on macOS (PyInstaller cannot cross-compile, so
# this whole script only makes sense run where deploy/build-installers.yml's
# macos-latest CI runner, or a real Mac, already has a macOS-built
# `dist/stingray`).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STAGE_DIR="$(mktemp -d)"
PKG_ROOT="${STAGE_DIR}/root"
SCRIPTS_DIR="${STAGE_DIR}/scripts"

mkdir -p "${PKG_ROOT}/usr/local/stingray" "${PKG_ROOT}/Library/LaunchDaemons" "${SCRIPTS_DIR}"

cp "${REPO_ROOT}/dist/stingray" "${PKG_ROOT}/usr/local/stingray/stingray"
chmod +x "${PKG_ROOT}/usr/local/stingray/stingray"
cp -r "${REPO_ROOT}/data" "${PKG_ROOT}/usr/local/stingray/data"
cp "${SCRIPT_DIR}/../.env.example" "${PKG_ROOT}/usr/local/stingray/.env"
cp "${SCRIPT_DIR}/com.stingray.planner.plist" "${PKG_ROOT}/Library/LaunchDaemons/"
cp "${SCRIPT_DIR}/com.stingray.capture.plist" "${PKG_ROOT}/Library/LaunchDaemons/"

cat > "${SCRIPTS_DIR}/postinstall" <<'EOF'
#!/bin/bash
chown -R root:wheel /usr/local/stingray
chmod 600 /usr/local/stingray/.env
launchctl load -w /Library/LaunchDaemons/com.stingray.planner.plist
# capture is installed but not auto-loaded -- see deploy/README.md, needs
# the gateway flags edited for this vessel's actual hardware first.
exit 0
EOF
chmod +x "${SCRIPTS_DIR}/postinstall"

pkgbuild \
  --root "${PKG_ROOT}" \
  --scripts "${SCRIPTS_DIR}" \
  --identifier com.stingray.planner \
  --version 0.1.0 \
  --install-location / \
  "${REPO_ROOT}/dist/stingray-installer.pkg"

echo "Built ${REPO_ROOT}/dist/stingray-installer.pkg"
rm -rf "${STAGE_DIR}"
