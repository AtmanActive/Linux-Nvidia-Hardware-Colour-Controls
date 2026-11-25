#!/usr/bin/env bash
# Per-user install of Linux Nvidia Hardware Colour Controls (no root needed).
#   ./install.sh                 install to ~/.local and enable autostart
#   PREFIX=/opt/x ./install.sh   install elsewhere
#   ./install.sh --no-autostart  do not create the login autostart entry
set -euo pipefail

APP=linuxnvidiahwcc
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
LIBDIR="$PREFIX/lib/$APP"
BINDIR="$PREFIX/bin"
APPDIR="$PREFIX/share/applications"
ICONDIR="$PREFIX/share/icons/hicolor/scalable/apps"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
AUTOSTART=1
[[ "${1:-}" == "--no-autostart" ]] && AUTOSTART=0

die() { echo "error: $*" >&2; exit 1; }

command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import PySide6.QtWidgets' 2>/dev/null || die "PySide6 not found (sudo pacman -S pyside6)"
command -v kscreen-doctor >/dev/null || die "kscreen-doctor not found (KDE Plasma 6 / libkscreen)"
command -v make >/dev/null && command -v cc >/dev/null || die "a C compiler and make are needed (sudo pacman -S base-devel)"
[[ -e /dev/nvidia-modeset ]] || echo "warning: /dev/nvidia-modeset missing; Digital Vibrance will be unavailable" >&2

echo "Building NVKMS helper..."
make -C "$SRC/helper" >/dev/null

echo "Installing to $PREFIX ..."
rm -rf "$LIBDIR"
mkdir -p "$LIBDIR" "$BINDIR" "$APPDIR" "$ICONDIR"
cp -r "$SRC/$APP" "$LIBDIR/"
find "$LIBDIR" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 "$SRC/helper/nvkms-vibrance" "$LIBDIR/$APP/nvkms-vibrance"
install -m 644 "$SRC/$APP/icons/$APP.svg" "$ICONDIR/$APP.svg"

cat > "$BINDIR/$APP" <<EOF
#!/bin/sh
exec env PYTHONPATH="$LIBDIR" python3 -m $APP "\$@"
EOF
chmod 755 "$BINDIR/$APP"

cat > "$APPDIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Linux Nvidia Hardware Colour Controls
GenericName=Display Colour Controls
Comment=NVIDIA hardware brightness, contrast, gamma and digital vibrance
Exec=$BINDIR/$APP --show
Icon=$APP
Terminal=false
Categories=Settings;HardwareSettings;Qt;
Keywords=nvidia;vibrance;gamma;brightness;contrast;colour;color;
StartupNotify=false
EOF

if [[ $AUTOSTART == 1 ]]; then
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_DIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Linux Nvidia Hardware Colour Controls
Comment=Apply NVIDIA hardware brightness, contrast, gamma and digital vibrance
Exec=$BINDIR/$APP
Icon=$APP
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-phase=2
EOF
fi

command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPDIR" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true

echo
echo "Installed. Helper check:"
"$LIBDIR/$APP/nvkms-vibrance" list >/dev/null && echo "  NVKMS helper OK (driver $(cat /sys/module/nvidia/version 2>/dev/null))" \
    || echo "  NVKMS helper failed; run helper/update-headers.sh to rebuild for your driver"
echo
echo "Start it now with:   $BINDIR/$APP --show"
[[ $AUTOSTART == 1 ]] && echo "It will also start minimised to the tray at every login."
