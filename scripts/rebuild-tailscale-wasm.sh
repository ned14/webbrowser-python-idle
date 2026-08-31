#!/bin/sh
# Rebuild the browser-side Tailscale wasm client from source
# (plans/networking-bug.md §16): the CheerpX runtime ships Leaning's private
# tsconnect fork (v1.78, capver 109, never driven by the core — §15.1), so
# this repo replaces it with a tailscale v1.102.2 build whose custom
# //go:build js entry (scripts/tailscale-wasm-entry/wasm_js.go) reproduces
# the CheerpX glue's API surface:
#   newIPN(conf) -> { tun, run, up, down, login, logout }
#     tun:  { onmessage, postMessage(data, transfer) }  (raw IP packets)
#     run({notifyState, notifyNetMap, notifyBrowseToURL})
#     notifyState: NUMERIC ipn.State (0=NoState ... 6=Running)
#     notifyNetMap: JSON string { self: {addresses, ...}, peers: [...] }
#
# Usage: ./scripts/rebuild-tailscale-wasm.sh
# Outputs: webvm/cheerpx/tun/tailscale.wasm  (then run build.sh / npm build)
#          webvm/cheerpx/tun/wasm_exec.js    (matching Go toolchain glue)
#
# Requires Docker (golang image; no local Go toolchain needed).
# Pinned: tailscale v1.102.2, Go 1.26.6 (tailscale's go.mod requirement).
# Versions come from scripts/versions.env — the single source for the
# tailscale pin (gateway image + join-test client must agree; the lockstep
# unit test enforces it).
set -eu

VERSION_FILE="$(dirname "$0")/versions.env"
if [ ! -f "$VERSION_FILE" ]; then
	echo "FATAL: $VERSION_FILE not found" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$VERSION_FILE"

VERSION="v${TAILSCALE_VERSION}"
GO_IMAGE="golang:${GO_VERSION}"
ENTRY="$(dirname "$0")/tailscale-wasm-entry/wasm_js.go"
DEST_TUN="webvm/cheerpx/tun"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "==> cloning tailscale ${VERSION} (shallow)"
git clone --depth 1 --branch "$VERSION" https://github.com/tailscale/tailscale.git "$BUILD_DIR/tailscale"

echo "==> installing the custom wasm entry"
cp "$ENTRY" "$BUILD_DIR/tailscale/cmd/tsconnect/wasm/wasm_js.go"

echo "==> building tailscale.wasm (GOOS=js GOARCH=wasm) in ${GO_IMAGE}"
docker run --rm -v "$BUILD_DIR/tailscale":/src -w /src "$GO_IMAGE" sh -c '
	set -eu
	GOOS=js GOARCH=wasm go build -o tailscale.wasm ./cmd/tsconnect/wasm
	cp "$(go env GOROOT)/lib/wasm/wasm_exec.js" ./wasm_exec.js
'

mkdir -p "$DEST_TUN"
cp "$BUILD_DIR/tailscale/tailscale.wasm" "$DEST_TUN/tailscale.wasm"
cp "$BUILD_DIR/tailscale/wasm_exec.js" "$DEST_TUN/wasm_exec.js"

echo "==> done:"
ls -la "$DEST_TUN/tailscale.wasm" "$DEST_TUN/wasm_exec.js"
echo "Next: ./build.sh webdav && (cd webvm && WEBVM_MODE=webdav WEBVM_IMAGE_BUILD=... npm run build)"
