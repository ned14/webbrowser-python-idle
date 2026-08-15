#!/bin/sh
# Fetch the CheerpX 1.3.7 runtime into webvm/cheerpx/ so the site serves it
# SAME-ORIGIN (zero external requests at page load; the pinned @leaningtech/
# cheerpx npm package is only a thin wrapper that CDN-loads its core by
# default).
#
# The files are committed to the repo (webvm/cheerpx/) so the frontend build
# stays deterministic and offline-safe beyond npm. Re-run this script ONLY to
# re-pin to a different CheerpX version (then update webvm/package.json and
# the version below).
set -eu

VERSION="1.3.7"
BASE="https://cxrtnc.leaningtech.com/${VERSION}"
DEST="webvm/cheerpx"

FILES="cx.esm.js cx_esm.js cxcore.js cxcore-no-return-call.js cxbridge.js
workerclock.js cheerpOS.js cxcore.wasm tun/direct.js tun/tailscale_tun_auto.js
tun/ipstack.wasm tun/ipstack.js tun/tailscale_tun.js"

# tailscale.wasm AND tun/wasm_exec.js are NOT fetched: the repo ships a
# REBUILT client (tailscale v1.102.2 + its matching Go 1.26.5 wasm_exec.js,
# scripts/rebuild-tailscale-wasm.sh) — the CDN's Leaning-fork pair is broken
# in every CheerpX runtime (plans/networking-bug.md §15/§16), and mixing the
# CDN glue with the rebuilt wasm breaks instantiation.

# Files the CDN serves as HTTP 204 (intentional empty placeholders) — commit
# them as empty files so the runtime's fetches resolve same-origin.
EMPTY_FILES="fail.wasm dump.wasm t.wasm tailscale_tun.js"

mkdir -p "$DEST/tun"

for f in $FILES; do
	curl -sL -o "$DEST/$f" "$BASE/$f"
	size=$(wc -c < "$DEST/$f")
	if [ "$size" -eq 0 ]; then
		echo "WARN: $f downloaded empty — check the CDN" >&2
	fi
	echo "  $f ($size bytes)"
done

for f in $EMPTY_FILES; do
	: > "$DEST/$f"
	echo "  $f (empty placeholder)"
done

echo "CheerpX runtime $VERSION fetched to $DEST/"
echo "The frontend imports it via src/lib/cheerpx.js (alias in vite.config.js)."
