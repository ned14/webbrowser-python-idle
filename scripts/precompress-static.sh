#!/bin/sh
# Precompress the built frontend (webvm/build) into brotli (.br) siblings so
# nginx can serve `brotli_static on` responses (server/nginx.conf.template)
# instead of compressing per request. The heavy assets — the ~31 MB CheerpX
# tailscale.wasm client and the SvelteKit chunks — are compressed once, at
# quality 11, at build time (~5 MB for tailscale.wasm vs ~7 MB gzipped).
#
# Deliberately NO .gz siblings: nginx runs built-in gzip_static BEFORE the
# dynamically loaded brotli_static in the PRECONTENT phase, so with both
# siblings present every gzip-capable client would receive the larger gzip
# representation and the br output would never be served. Clients without br
# support fall through to nginx's runtime gzip.
#
# Wired into webvm/package.json's "build" script, so every frontend build
# path (make build, CI ci.yml, CI pages.yml) produces the siblings.
#
# Text-ish and wasm types only: images/fonts are already compressed formats
# (and brotli_static would never pick a sibling for them anyway if absent).
# Skips files < 1 KiB — compression overhead is not worth it below that.
set -eu

BUILD_DIR="${1:-build}"
if [ ! -d "$BUILD_DIR" ]; then
	echo "precompress-static: build dir not found: $BUILD_DIR" >&2
	exit 1
fi

if ! command -v brotli >/dev/null 2>&1; then
	echo "precompress-static: brotli CLI not found; skipping precompression" \
		"(install brotli for .br siblings; runtime gzip still covers clients)" >&2
	exit 0
fi

find "$BUILD_DIR" -type f \
	\( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' \
		-o -name '*.svg' -o -name '*.wasm' \) \
	-size +1024c | while IFS= read -r file; do
	brotli -q 11 -c "$file" > "$file.br"
	printf '  br  %s\n' "$file.br"
done

echo "precompress-static: done ($BUILD_DIR)"
