#!/usr/bin/env bash
# Install a built package with the distribution's own package manager (which
# proves the dependency names resolve) and check that the app loads.
#
#   packaging/smoke-test.sh <fedora|debian|cachyos> <package-file>
#
# Run as root inside a container of the target distribution. No display,
# GPU or KDE session is needed.
set -euo pipefail

NAME=linuxnvidiahwcc
TARGET=${1:?usage: smoke-test.sh <fedora|debian|cachyos> <package-file>}
PKG=$(realpath "${2:?usage: smoke-test.sh <fedora|debian|cachyos> <package-file>}")

case "$TARGET" in
    fedora) dnf -y install --setopt=install_weak_deps=False "$PKG" ;;
    debian)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -q
        apt-get install -y -q --no-install-recommends "$PKG"
        ;;
    cachyos | arch) pacman -U --noconfirm "$PKG" ;;
    *) echo "unknown target '$TARGET'" >&2; exit 1 ;;
esac

echo "--- launcher"
"$NAME" --version

echo "--- runtime tools"
command -v kscreen-doctor
command -v busctl

echo "--- helper binary (runs and links; no GPU needed for the usage message)"
HELPER=/usr/lib/$NAME/$NAME/nvkms-vibrance
out=$("$HELPER" 2>&1 || true)  # without arguments it prints usage and exits 1
case "$out" in
    usage:*) echo "$HELPER OK" ;;
    *) echo "error: helper did not run: $out" >&2; exit 1 ;;
esac

echo "--- application modules and Qt"
QT_QPA_PLATFORM=offscreen PYTHONSAFEPATH=1 PYTHONPATH=/usr/lib/$NAME python3 - <<'EOF'
import sys
from PySide6.QtWidgets import QApplication
from linuxnvidiahwcc import app, config, engine, icc, nvkms

assert nvkms.helper_path() == "/usr/lib/linuxnvidiahwcc/linuxnvidiahwcc/nvkms-vibrance", nvkms.helper_path()
assert icc.build_profile(brightness=60, contrast=55, gamma=1.2)[36:40] == b"acsp"

qa = QApplication(sys.argv)
eng = engine.Engine(config.Config({}, {}))
window = app.MainWindow(eng, app.Worker(eng))  # builds the whole UI, offscreen
print("UI built:", window.windowTitle())
EOF

echo "--- desktop entry and icon"
test -f /usr/share/applications/$NAME.desktop
test -f /usr/share/icons/hicolor/scalable/apps/$NAME.svg
echo "Smoke test passed for $(basename "$PKG")"
