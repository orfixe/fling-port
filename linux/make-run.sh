#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Builds dist/Fling-Setup-<version>-linux.run: one file a user downloads and opens.
# It is a short shell script followed by a .tar.gz of desktop/ + linux/; run, it unpacks to a
# temporary folder, checks the payload's SHA-256 and runs linux/install.sh from there.
# The archive is reproducible (LF line endings, sorted names, fixed times and owners): the same
# commit gives the same file again. release.ps1 builds it with Git Bash and signs its SHA-256 in
# SHA256SUMS.txt with the other release files.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VER="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$REPO/desktop/fling_app.py")"
OUT="${1:-$REPO/dist}/Fling-Setup-$VER-linux.run"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT

P="$STAGE/fling-$VER"
mkdir -p "$P/desktop" "$P/linux"
cp "$REPO"/desktop/*.py "$P/desktop/"
cp "$REPO"/linux/{install.sh,fling,fling-tui,bt_remote.py,requirements.txt} "$P/linux/"
cp "$REPO/docs/screenshots/logo.png" "$P/linux/fling.png"
cp "$REPO"/{LICENSE,NOTICE,CREDITS.md,THIRD-PARTY-NOTICES.md} "$P/"
# a Windows checkout (core.autocrlf) has CRLF: bash scripts would not run, the hash would differ
sed -i 's/\r$//' "$P"/desktop/*.py "$P"/linux/{install.sh,fling,fling-tui,bt_remote.py,requirements.txt} "$P"/*.md "$P/LICENSE" "$P/NOTICE"
chmod 644 "$P"/desktop/* "$P"/linux/* "$P"/*.md "$P/LICENSE" "$P/NOTICE"
chmod 755 "$P"/linux/{install.sh,fling,fling-tui}

MTIME="$(git -C "$REPO" log -1 --format=%cI 2>/dev/null || echo 2026-01-01T00:00:00Z)"
tar -C "$STAGE" --sort=name --mtime="$MTIME" --owner=0 --group=0 --numeric-owner --mode=u+w,go-w \
    --format=gnu -cf - "fling-$VER" | gzip -9n > "$STAGE/payload.tgz"
SUM="$(sha256sum "$STAGE/payload.tgz" | cut -d' ' -f1)"

mkdir -p "$(dirname "$OUT")"
{
sed -e "s/@VER@/$VER/g" -e "s/@SUM@/$SUM/g" <<'HEAD'
#!/usr/bin/env bash
# Fling @VER@ for Linux — installer. Open it (double-click, or: bash Fling-Setup-@VER@-linux.run).
# Installs into ~/.local for this user; nothing goes outside your home folder unless you
# agree to the optional system packages. Options are passed to install.sh:
#   --no-system   never ask for a password     --uninstall   remove Fling
#   --extract DIR only unpack the files to DIR (to read them first)
# Unofficial port of Fling by Damion Rashford and Tatiana - MIT, see CREDITS.md and NOTICE.
set -euo pipefail
VER=@VER@
SUM=@SUM@
SELF="$(readlink -f "$0")"

# opened from a file manager: no terminal, so show the installer in one
if [[ ! -t 0 && -z ${FLING_SETUP_TERM:-} && -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]]; then
    export FLING_SETUP_TERM=1
    run=(bash "$SELF" "$@")
    for t in konsole kgx gnome-terminal xfce4-terminal mate-terminal lxterminal alacritty kitty foot xterm x-terminal-emulator; do
        command -v "$t" >/dev/null || continue
        case $t in
            konsole)        exec konsole --hide-menubar -e "${run[@]}" ;;
            gnome-terminal|kgx) exec "$t" -- "${run[@]}" ;;
            xfce4-terminal|mate-terminal|lxterminal) exec "$t" -x "${run[@]}" ;;
            *)              exec "$t" -e "${run[@]}" ;;
        esac
    done
fi
pause() { [[ -n ${FLING_SETUP_TERM:-} ]] && { echo; read -r -p "Press Enter to close this window. " _ || true; }; return 0; }

line=$(awk '/^__FLING_PAYLOAD__$/ { print NR + 1; exit }' "$SELF")
if [[ ${1:-} == --extract ]]; then
    mkdir -p "${2:?folder}" && tail -n +"$line" "$SELF" | tar -xz -C "$2" && echo "Unpacked to $2/fling-$VER"
    exit
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
tail -n +"$line" "$SELF" > "$TMP/payload.tgz"
if [[ "$(sha256sum "$TMP/payload.tgz" | cut -d' ' -f1)" != "$SUM" ]]; then
    echo "This file is damaged (checksum mismatch). Download Fling-Setup-$VER-linux.run again." >&2
    pause; exit 1
fi
tar -xzf "$TMP/payload.tgz" -C "$TMP"
status=0
bash "$TMP/fling-$VER/linux/install.sh" "$@" || status=$?
[[ $status -ne 0 ]] && echo "Installation failed (code $status)." >&2
pause
exit $status
__FLING_PAYLOAD__
HEAD
cat "$STAGE/payload.tgz"
} > "$OUT"
chmod 755 "$OUT"
echo "$OUT"
sha256sum "$OUT"
