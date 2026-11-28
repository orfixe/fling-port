#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Installs Fling for Linux into ~/.local. Works from a git checkout and from the unpacked
# Fling-Setup-<version>-linux.run (same layout: linux/ next to desktop/).
#   ./install.sh                 install / update
#   ./install.sh --no-system     never ask for a password (skip the optional system packages)
#   ./install.sh --uninstall
#
# No root is needed. PyQt6 and lz4 come from the system if it has them, otherwise from PyPI
# into Fling's own venv; adb and scrcpy from the system, otherwise Fling downloads the official
# builds and checks their checksums. Only D-Bus / PyGObject (KDE notifications, the Bluetooth
# remote) are system-only: they are offered with sudo / pkexec and skipped if declined.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
DATA="$HOME/.local/share/fling"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/64x64/apps"
SYSTEM=1
for a in "$@"; do
    case "$a" in
        --no-system) SYSTEM=0 ;;
        --uninstall) ;;
        -h|--help) sed -n 5,14p "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }

if [[ " $* " == *" --uninstall "* ]]; then
    "$BIN/fling" quit 2>/dev/null || true
    rm -rf "$DATA" "$BIN/fling" "$BIN/fling-tui" "$APPS/fling.desktop" "$APPS/fling-tui.desktop" \
           "$APPS/fling-cast-tab.desktop" "$HOME/.config/autostart/fling.desktop" "$ICONS/fling.png"
    command -v uv >/dev/null && uv tool uninstall catt 2>/dev/null || true
    echo "Fling removed (settings kept in ~/.config/fling.json)."
    exit 0
fi

[[ $EUID -eq 0 ]] && { echo "Run this as your normal user, not as root: Fling installs into ~/.local." >&2; exit 1; }
[[ -f "$SRC/desktop/fling_app.py" ]] || { echo "desktop/fling_app.py not found next to linux/" >&2; exit 1; }
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/desktop/fling_app.py")"
say "Fling $VERSION"

# ---------------------------------------------------------------- what the system already has
PY=""
for p in /usr/bin/python3 python3; do
    if command -v "$p" >/dev/null && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$(command -v "$p")"; break
    fi
done
has_py() { [[ -n $PY ]] && "$PY" -c "import $1" 2>/dev/null; }

# package names: PyQt6, dbus-python, PyGObject, lz4, adb, scrcpy, parecord, xprop
pm="" pkgs=()
if   command -v apt-get >/dev/null; then pm=apt;    n=(python3-pyqt6 python3-dbus python3-gi python3-lz4 adb scrcpy pulseaudio-utils x11-utils)
elif command -v pacman  >/dev/null; then pm=pacman; n=(python-pyqt6 python-dbus python-gobject python-lz4 android-tools scrcpy libpulse xorg-xprop)
elif command -v dnf     >/dev/null; then pm=dnf;    n=(python3-pyqt6 python3-dbus python3-gobject python3-lz4 android-tools scrcpy pulseaudio-utils xprop)
elif command -v zypper  >/dev/null; then pm=zypper; n=(python3-PyQt6 python3-dbus-python python3-gobject python3-lz4 android-tools scrcpy pulseaudio-utils xprop)
fi
if [[ -n $pm ]]; then
    has_py PyQt6           || pkgs+=("${n[0]}")
    has_py dbus            || pkgs+=("${n[1]}")
    has_py gi              || pkgs+=("${n[2]}")
    has_py lz4.block       || pkgs+=("${n[3]}")
    command -v adb      >/dev/null || pkgs+=("${n[4]}")
    command -v scrcpy   >/dev/null || pkgs+=("${n[5]}")
    command -v parecord >/dev/null || pkgs+=("${n[6]}")
    command -v xprop    >/dev/null || pkgs+=("${n[7]}")
fi

say "System packages"
if [[ ${#pkgs[@]} -eq 0 ]]; then
    note "everything needed is already installed"
elif [[ $SYSTEM -eq 0 || -z $pm ]]; then
    [[ -z $pm ]] && note "unknown package manager: Fling will use its own copies where it can"
    note "skipped: ${pkgs[*]}"
else
    note "missing: ${pkgs[*]}"
    case $pm in
        apt)    cmd=(apt-get install -y "${pkgs[@]}") ;;
        pacman) cmd=(pacman -S --needed --noconfirm "${pkgs[@]}") ;;
        dnf)    cmd=(dnf install -y "${pkgs[@]}") ;;
        zypper) cmd=(zypper --non-interactive install "${pkgs[@]}") ;;
    esac
    ok=0
    if [[ -t 0 ]] && command -v sudo >/dev/null; then
        read -r -p "    Install them with sudo (asks your password)? [Y/n] " ans || ans=n
        if [[ ! $ans =~ ^[Nn] ]]; then
            # one package at a time on failure: a name missing on this release must not block the rest
            sudo "${cmd[@]}" && ok=1 || for p in "${pkgs[@]}"; do sudo "${cmd[@]:0:${#cmd[@]}-${#pkgs[@]}}" "$p" || warn "not installed: $p"; done
        fi
    elif command -v pkexec >/dev/null && [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]]; then
        note "asking for the password in a system dialog..."
        pkexec "${cmd[@]}" && ok=1 || warn "the system packages were not installed"
    else
        warn "no terminal for sudo: skipped (run again in a terminal to add them)"
    fi
    [[ $ok -eq 1 ]] && note "done"
fi

# ---------------------------------------------------------------- uv, catt, the venv
say "uv + catt (the Cast engine)"
if ! command -v uv >/dev/null && [[ ! -x $BIN/uv ]]; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN" UV_NO_MODIFY_PATH=1 sh
fi
UV="$(command -v uv || echo "$BIN/uv")"
"$UV" tool install --upgrade catt

say "Python environment"
mkdir -p "$DATA" "$BIN" "$APPS" "$ICONS" "$HOME/.cache/fling"
if [[ -n $PY ]]; then
    "$UV" venv -q --allow-existing --system-site-packages --python "$PY" "$DATA/venv"
else
    note "no Python 3.10+ on the system: uv brings its own"
    "$UV" venv -q --allow-existing --python 3.13 "$DATA/venv"
fi
VPY="$DATA/venv/bin/python"
req=(-r "$HERE/requirements.txt")
"$VPY" -c 'import PyQt6.QtWidgets' 2>/dev/null || { note "PyQt6 from PyPI"; req+=("PyQt6>=6.6"); }
"$VPY" -c 'import lz4.block'       2>/dev/null || { note "lz4 from PyPI";   req+=("lz4>=4"); }
"$UV" pip install -q --python "$VPY" "${req[@]}"

# ---------------------------------------------------------------- files
say "Files"
running=0
pgrep -u "$USER" -f "$DATA/fling_app.py" >/dev/null 2>&1 && running=1
install -m 644 "$SRC"/desktop/*.py "$HERE/bt_remote.py" "$DATA/"
install -m 755 "$HERE/fling" "$HERE/fling-tui" "$BIN/"
icon=video-display
for f in "$HERE/fling.png" "$SRC/docs/screenshots/logo.png"; do
    [[ -f $f ]] && { install -m 644 "$f" "$ICONS/fling.png"; icon=fling; break; }
done

cat > "$APPS/fling.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fling
Comment=Cast to Chromecast / Google TV and control the TV
Comment[ru]=Трансляция на Chromecast / Google TV и пульт телевизора
Exec=$BIN/fling
Icon=$icon
Categories=AudioVideo;Video;
StartupNotify=false
EOF
cat > "$APPS/fling-tui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fling (terminal)
Exec=$BIN/fling-tui
Terminal=true
Icon=utilities-terminal
Categories=AudioVideo;
EOF
cat > "$APPS/fling-cast-tab.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fling: cast current tab
Exec=$BIN/fling cast-tab
Icon=$icon
NoDisplay=true
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" 2>/dev/null || true

if command -v kwriteconfig6 >/dev/null; then
    note "KDE shortcut Meta+Shift+C → cast the current Firefox tab (active after re-login)"
    kwriteconfig6 --file kglobalshortcutsrc --group services --group fling-cast-tab.desktop --key _launch "Meta+Shift+C"
    kbuildsycoca6 >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------- adb / scrcpy Fling's own copies
say "adb + scrcpy"
(cd "$DATA" && QT_QPA_PLATFORM=offscreen "$VPY" -I - <<'PY') || warn "Fling → Tools can install them later"
import sys
sys.path.insert(0, ".")
import fling_tools as T
bad = 0
for key in ("adb", "scrcpy"):
    have = T.best(key, T.scan(key))        # a usable copy: the system's or Fling's own
    if have:
        print(f"    {key}: {have.path} {have.version or ''}")
        continue
    print(f"    {key}: downloading the official build (checksum verified)...", flush=True)
    try:
        c = T.run_now(key)
        print(f"    {key}: {c.path} {c.version or ''}")
    except Exception as e:
        print(f"    ! {key} was not installed: {e}")
        bad = 1
sys.exit(bad)
PY

# ---------------------------------------------------------------- start
if [[ $running -eq 1 ]]; then
    say "Restarting Fling"
    "$BIN/fling" quit 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -u "$USER" -f "$DATA/fling_app.py" >/dev/null || break; sleep 0.5; done
    "$BIN/fling" tray || true
fi

echo
echo "Fling $VERSION is installed. Start it from the app menu or with:  fling"
echo "Then open  Fling → Connect TV…  and press  Find and connect everything."
case ":$PATH:" in *":$BIN:"*) ;; *) echo "(Add ~/.local/bin to PATH to run  fling  from a terminal.)" ;; esac
