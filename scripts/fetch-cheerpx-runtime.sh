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

# Fetch into a STAGING dir first (never directly over the committed files):
# an interrupted fetch or a CDN error body must not corrupt the repo's
# runtime. Only after every download, the trampoline patch AND its guards
# pass is the staging tree installed into $DEST.
STAGE="$DEST/.fetch-stage-$$"
mkdir -p "$STAGE/tun"
trap 'rm -rf "$STAGE"' EXIT

for f in $FILES; do
	mkdir -p "$(dirname "$STAGE/$f")"
	curl -sL --fail -o "$STAGE/$f" "$BASE/$f" || {
		echo "ERROR: CDN fetch failed for $f — repo runtime untouched" >&2
		exit 1
	}
	size=$(wc -c < "$STAGE/$f")
	if [ "$size" -eq 0 ]; then
		echo "WARN: $f downloaded empty — check the CDN" >&2
	fi
	echo "  $f ($size bytes)"
done

for f in $EMPTY_FILES; do
	mkdir -p "$(dirname "$STAGE/$f")"
	: > "$STAGE/$f"
	echo "  $f (empty placeholder)"
done

# Apply the vendored runtime patch to the two cxcore trampoline files (see
# plans/webvm_implementation.md §12/21(32)). The pinned 1.3.7 core SWALLOWS
# guest-side WASM traps at its thread trampolines — it `debugger;`-pauses
# (freezing the whole tab whenever DevTools is open), `console.log`s
# "Unexpected exit <err>" and then silently carries on, so a boot-critical
# guest process can die with cx.run() never rejecting and the web app (and
# user) never finding out. One trampoline even CALLS the caught exception
# object (`e()`), throwing a spurious uncaught `TypeError: e is not a
# function` that wedges the runtime and masks the real error. The patch:
#   * removes every `debugger;` statement (they had no effect unless the
#     DevTools console is open — exactly when you need a stable VM);
#   * drops the `e()` call;
#   * reports EVERY swallowed trap via the SAME `console.error('Unexpected
#     exit', …)` prefix so WebVM.svelte's trap capture can surface the exact
#     reason from any of the three trampolines.
# The guards are presence-based, not mere absence checks: after patching the
# file MUST contain exactly three `console.error('Unexpected exit'` sites
# (one per trampoline) and no `debugger`/`e()` calls. The patch is applied
# to the STAGING copy only — any failure leaves the committed runtime
# byte-unchanged.
patch_cxcore() {
	file="$1"
	if [ "$(grep -o "console.error('Unexpected exit'" "$file" 2>/dev/null | wc -l | tr -d ' ')" = "3" ]; then
		echo "  $(basename "$file") already patched (3 trap-report sites present)"
		return 0
	fi
	patched=$(cat "$file" | perl -0pe "s/catch\(e\)\{if\(e!='CheerpJContinue'\)\{debugger;console\.log\('Unexpected exit',e\.stack\);e\(\)\}\}/catch(e){if(e!='CheerpJContinue'){console.error('Unexpected exit',e.stack);}}/g")
	patched=$(printf '%s' "$patched" | perl -0pe "s/catch\(e\)\{if\(e!='CheerpJContinue'\)\{debugger;console\.log\('Unexpected exit',e\.stack\);\}\}/catch(e){if(e!='CheerpJContinue'){console.error('Unexpected exit',e.stack);}}/g")
	patched=$(printf '%s' "$patched" | perl -0pe "s/catch\(e\)\{if\(e!='CheerpJContinue'\)debugger\}/catch(e){if(e!='CheerpJContinue')console.error('Unexpected exit',e)}/g")
	patched=$(printf '%s' "$patched" | perl -0pe 's/debugger;/void 0;/g')
	case "$patched" in
		*debugger*)
			echo "ERROR: $file still contains 'debugger' after patching" >&2
			return 1
			;;
		*"e.stack);e()"*)
			echo "ERROR: $file still contains the exception-object call" >&2
			return 1
			;;
	esac
	sites=$(printf '%s' "$patched" | grep -o "console.error('Unexpected exit'" | wc -l | tr -d ' ')
	if [ "$sites" != "3" ]; then
		echo "ERROR: $file has $sites/3 trap-report sites after patching" >&2
		return 1
	fi
	printf '%s' "$patched" > "$file"
	echo "  $(basename "$file") patched (trampoline trap reporting + no debugger freeze)"
}

for f in cxcore.js cxcore-no-return-call.js; do
	patch_cxcore "$STAGE/$f" || exit 1
done

# Everything verified: atomically move the staging tree into place.
for f in $FILES $EMPTY_FILES; do
	cp "$STAGE/$f" "$DEST/$f"
done
rm -rf "$STAGE"
trap - EXIT

echo "CheerpX runtime $VERSION fetched to $DEST/"
echo "The frontend imports it via src/lib/cheerpx.js (alias in vite.config.js)."
