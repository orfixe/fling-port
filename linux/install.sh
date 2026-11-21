#!/usr/bin/env bash
# Installs Fling for Linux into ~/.local (no root needed except for system packages).
#   ./install.sh            install / update
#   ./install.sh --uninstall
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HOME/.local/share/fling"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

if [[ "${1:-}" == "--uninstall" ]]; then
    "$BIN/fling" quit 2>/dev/null || true
    rm -rf "$DATA" "$BIN/fling" "$BIN/fling-tui" "$APPS/fling.desktop" "$APPS/fling-tui.desktop" \
           "$APPS/fling-cast-tab.desktop" "$HOME/.config/autostart/fling.desktop"
    command -v uv >/dev/null && uv tool uninstall catt 2>/dev/null || true
    echo "Fling removed (settings kept in ~/.config/fling.json)."
    exit 0
fi

echo "==> System packages (PyQt6, D-Bus, lz4, adb)"
if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3-pyqt6 python3-dbus python3-gi python3-lz4 adb pulseaudio-utils x11-utils
else
    echo "   Not Debian/Ubuntu: install PyQt6, dbus-python, PyGObject, lz4, adb, parecord, xprop yourself."
fi

echo "==> uv + catt (the Cast engine)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
"$UV" tool install --upgrade catt

echo "==> Python environment"
mkdir -p "$DATA" "$BIN" "$APPS" "$HOME/.cache/fling"
"$UV" venv --allow-existing --system-site-packages --python /usr/bin/python3 "$DATA/venv"
"$UV" pip install --python "$DATA/venv/bin/python" -r "$HERE/requirements.txt"

echo "==> Files"
install -m 644 "$HERE/fling_app.py" "$HERE/bt_remote.py" "$HERE/sf_icons.py" "$DATA/"
install -m 755 "$HERE/fling" "$HERE/fling-tui" "$BIN/"

cat > "$APPS/fling.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fling
Comment=Cast to Chromecast / Google TV and control the TV
Exec=$BIN/fling
Icon=video-display
Categories=AudioVideo;Video;
StartupNotify=false
EOF
cat > "$APPS/fling-tui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fling (terminal)
Exec=konsole --hide-menubar -e $BIN/fling-tui
Icon=utilities-terminal
Categories=AudioVideo;
EOF
cat > "$APPS/fling-cast-tab.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fling: cast current tab
Exec=$BIN/fling cast-tab
Icon=video-display
NoDisplay=true
EOF

if command -v kwriteconfig6 >/dev/null; then
    echo "==> Global shortcut Meta+Shift+C → cast current Firefox tab (active after re-login)"
    kwriteconfig6 --file kglobalshortcutsrc --group services --group fling-cast-tab.desktop --key _launch "Meta+Shift+C"
    kbuildsycoca6 >/dev/null 2>&1 || true
fi

echo
echo "Done. Start it with:  fling"
echo "Then open  Fling → Connect TV…  and press  Find and connect everything."
