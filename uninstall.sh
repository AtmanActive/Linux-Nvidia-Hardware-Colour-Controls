#!/usr/bin/env bash
# Remove Linux Nvidia Hardware Colour Controls. Resets the displays to neutral first so KWin is not
# left pointing at a profile that is about to be deleted.
#   ./uninstall.sh           remove program files, keep settings
#   ./uninstall.sh --purge   also remove settings and generated profiles
set -euo pipefail

APP=linuxnvidiahwcc
PREFIX="${PREFIX:-$HOME/.local}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

# anchored, so only the app itself matches (not shells/editors mentioning its name)
pkill -f "^([^ ]*/)?python3(\.[0-9]+)? -m $APP( |$)" 2>/dev/null || true
if [[ -x "$PREFIX/bin/$APP" ]]; then
    echo "Resetting displays to neutral..."
    "$PREFIX/bin/$APP" --reset >/dev/null || echo "warning: reset failed; check System Settings > Display" >&2
fi

rm -rf "$PREFIX/lib/$APP"
rm -f "$PREFIX/bin/$APP" "$PREFIX/share/applications/$APP.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/$APP.svg" "$CONF/autostart/$APP.desktop"

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$CONF/$APP" "$DATA/$APP"
    echo "Removed program, settings and profiles."
else
    echo "Removed program. Settings kept in $CONF/$APP (use --purge to remove)."
fi
