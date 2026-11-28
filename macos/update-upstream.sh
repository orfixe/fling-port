#!/usr/bin/env bash
# Bring a newer upstream Fling into this port.
#   ./macos/update-upstream.sh [ref]      (default: upstream main)
# For every file in macos/Sources/ that upstream also has, three-way merges
#   upstream-base (what our copy was made from) → upstream ref (theirs) into our copy.
# Conflicts are left as <<<<<<< markers in macos/Sources/ and listed at the end.
# Then macos/upstream-base/ and macos/UPSTREAM_REF move to the new ref.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM_URL="${FLING_UPSTREAM:-https://github.com/tatianathevisionary/fling.git}"
NEW_REF="${1:-main}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

git clone --quiet "$UPSTREAM_URL" "$TMP/up"
git -C "$TMP/up" checkout --quiet "$NEW_REF"
NEW_SHA="$(git -C "$TMP/up" rev-parse HEAD)"
OLD_SHA="$(cat "$ROOT/macos/UPSTREAM_REF")"
echo "upstream: $OLD_SHA → $NEW_SHA"
[ "$OLD_SHA" = "$NEW_SHA" ] && { echo "already up to date"; exit 0; }

conflicts=()
cd "$ROOT/macos/Sources"
while IFS= read -r f; do
  rel="Sources/${f#./}"
  theirs="$TMP/up/$rel"
  base="$ROOT/macos/upstream-base/$rel"
  [ -f "$theirs" ] || continue              # our own file, upstream doesn't have it
  if [ ! -f "$base" ]; then                  # upstream added a file we also have
    echo "  ! $rel: upstream now has its own copy — compare by hand"; conflicts+=("$rel"); continue
  fi
  if ! git merge-file -L ours -L upstream-base -L "upstream $NEW_REF" "$f" "$base" "$theirs"; then
    conflicts+=("$rel")
  fi
  cp "$theirs" "$base"
done < <(find . -type f -name '*.swift')

echo "$NEW_SHA" > "$ROOT/macos/UPSTREAM_REF"
echo "upstream changes since the old base:"
git -C "$TMP/up" log --oneline "$OLD_SHA..$NEW_SHA" 2>/dev/null | head -50 || true
if [ ${#conflicts[@]} -gt 0 ]; then
  echo "CONFLICTS (fix the <<<<<<< blocks, then ./macos/build.sh):"; printf '  %s\n' "${conflicts[@]}"; exit 1
fi
echo "merged cleanly — now ./macos/build.sh"
