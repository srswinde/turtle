#!/usr/bin/env bash
# Vendor the sibling cassini_detect package into swahle/vendor/ so Dockerfile.ml
# can install it (the Docker build context is the turtle_db repo root and can't
# reach sibling repos). Run before `docker compose build`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWAHLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CASSINI_SRC="${CASSINI_DETECT_DIR:-$SWAHLE_DIR/../../cassini_detect}"

if [ ! -f "$CASSINI_SRC/setup.py" ]; then
    echo "ERROR: cassini_detect not found at $CASSINI_SRC" >&2
    echo "Set CASSINI_DETECT_DIR to its location and re-run." >&2
    exit 1
fi

DEST="$SWAHLE_DIR/vendor/cassini_detect"
rm -rf "$DEST"
mkdir -p "$DEST"
cp "$CASSINI_SRC/setup.py" "$DEST/"
cp -r "$CASSINI_SRC/cassini_detect" "$DEST/cassini_detect"

echo "Vendored cassini_detect -> $DEST"
