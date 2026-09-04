#!/bin/sh
# WebVM guest image build pipeline:
#   docker build --platform=linux/i386 (guest rootfs, backend-selected)
#   docker create / docker export -> rootfs.tar
#   untar + mkfs.ext2 -d INSIDE a throwaway e2fsprogs container (container-local
#   path, so uid/gid survive — never into a macOS-mounted dir, which remaps
#   uids) — only the final image.ext2 is written back to the host
#   compute the content-stable fingerprint (used as the cacheId suffix)
#
# Usage: ./build.sh [STORAGE_BACKEND]
# Outputs: webvm/custom-disk-images/webvm-custom-disk.ext2
#          webvm/custom-disk-images/image-build.txt (the fingerprint)
set -eu

# --- 0. Load deployment values from .env ------------------------------------
# docker compose reads .env directly; the build MUST resolve the same way, or
# the guest image can silently disagree with the deployment — e.g. a
# browser-mode image in a webdav deployment, where desktop.start never starts
# the sync agent and the guest never touches the network (fixed 2026-08-15).
# Precedence: command line > environment > .env > default (browser). The
# shared loader (scripts/lib/webvm-common.sh) applies the .env layer with the
# same semantics as every other script: an explicit env var always wins, and
# surrounding quotes are stripped (compose strips them too).
WEBVM_COMMON="${WEBVM_COMMON:-scripts/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"
webvm_load_dotenv

# CWD guard: the pipeline writes rootfs.tar, image.ext2 and webvm/
# custom-disk-images relative to the repo root, and webvm_load_dotenv reads
# ./.env — a wrong working directory would silently build from/into the
# wrong tree (or read a foreign .env). Fail with a clear message instead.
if [ ! -d diskimage ] || [ ! -d scripts ] || [ ! -f scripts/lib/webvm-common.sh ]; then
	echo "FATAL: build.sh must run from the repo root (no diskimage/scripts tree here)." >&2
	exit 1
fi

BACKEND_SOURCE="default (browser)"
if [ -n "${1:-}" ]; then
	BACKEND_SOURCE="command line"
elif [ -n "${STORAGE_BACKEND:-}" ]; then
	BACKEND_SOURCE="environment"
elif [ -f .env ] && grep -q '^[[:space:]]*STORAGE_BACKEND=' .env; then
	BACKEND_SOURCE=".env"
fi

STORAGE_BACKEND="${1:-${STORAGE_BACKEND:-browser}}"
case "$STORAGE_BACKEND" in
	browser|samba|webdav|none) ;;
	*) echo "Unknown STORAGE_BACKEND: $STORAGE_BACKEND (browser|samba|webdav|none)" >&2; exit 1 ;;
esac
echo "==> Backend: $STORAGE_BACKEND (from $BACKEND_SOURCE)"

IMAGE_TAG="webvm-guest"
# Where the rootfs is unpacked before mkfs (inside the throwaway helper
# container). On macOS the mounted /work is a macOS filesystem that remaps
# uid/gid, so we MUST unpack into a container-local path (/tmp/rootfs) for
# ownership to survive. On Linux /work is a native Linux dir (uid-safe), and
# unpacking there instead of /tmp avoids container-local /tmp quirks (a small
# or full tmpfs can silently truncate the extraction on some hosts, leaving an
# undersized rootfs that mkfs.ext2 then fills — the "Could not allocate block"
# / "No such file or directory" build failures seen on a remote Linux box).
EXTRACT_DIR=/tmp/rootfs
case "$(uname -s)" in
	Linux) EXTRACT_DIR=/work/.rootfs ;;
esac
# Image name + serving dir come from the shared lib (single home — nginx
# aliases the same values via the entrypoint's envsubst; the frontend
# literal is pinned by tests/unit/test_scripts.py).
OUT_DIR="webvm/${WEBVM_IMAGE_DIR}"
OUT_IMAGE="$OUT_DIR/$WEBVM_IMAGE_NAME"
FINGERPRINT_FILE="$OUT_DIR/image-build.txt"

# Clean up the credential-bearing export tarball and the export container even
# when a later step fails (set -eu would otherwise leave rootfs.tar behind).
cleanup() {
	docker rm -f webvm-guest-export >/dev/null 2>&1 || true
	rm -f rootfs.tar
	# A Linux build that aborts INSIDE the helper container (before its own
	# `rm -rf "$exroot"`) leaves the host-mounted extraction tree behind. It
	# is root-owned (the container extracts as root), so this best-effort rm
	# can fail for the non-root user — warn instead of masking the failure.
	case "$(uname -s)" in
		Linux)
			if ! rm -rf .rootfs 2>/dev/null; then
				echo "WARNING: could not remove .rootfs (root-owned leftovers from an aborted" >&2
				echo "         container run). Remove it with:  sudo rm -rf .rootfs" >&2
			fi
			;;
	esac
}
trap cleanup EXIT

# Effective build args (same defaults as diskimage/Dockerfile), reused by the
# fingerprint so CI and local builds agree on content-identical images.
# The WebDAV sync creds fall back to the deployment's WEBDAV_USER/WEBDAV_PASS
# (the server's wsgidav credentials — the baked /root/.syncrc must match them,
# or the no-injection fallback is guaranteed wrong); SYNC_* overrides exist
# for share-specific values.
SYNC_URL_EFF="${SYNC_URL:-http://${GATEWAY_TAILNET_IP_DEFAULT}:${WEBDAV_PORT}${WEBDAV_BASE_PATH}}"
SYNC_USER_EFF="${SYNC_USER:-${WEBDAV_USER:-webdav}}"
SYNC_PASS_EFF="${SYNC_PASS:-${WEBDAV_PASS:-changeme}}"
SAMBA_HOST_EFF="${SAMBA_HOST:-${GATEWAY_TAILNET_IP:-$GATEWAY_TAILNET_IP_DEFAULT}}"
SAMBA_SHARE_EFF="${SAMBA_SHARE:-share}"
SAMBA_USER_EFF="${SAMBA_USER:-user}"
SAMBA_PASS_EFF="${SAMBA_PASS:-changeme}"

mkdir -p "$OUT_DIR"

# --- 1. Build the guest rootfs image --------------------------------------
echo "==> Building guest rootfs ($STORAGE_BACKEND)"
docker build --platform=linux/i386 -t "$IMAGE_TAG" \
	--build-arg "STORAGE_BACKEND=$STORAGE_BACKEND" \
	--build-arg "SYNC_URL=$SYNC_URL_EFF" \
	--build-arg "SYNC_USER=$SYNC_USER_EFF" \
	--build-arg "SYNC_PASS=$SYNC_PASS_EFF" \
	--build-arg "SAMBA_HOST=$SAMBA_HOST_EFF" \
	--build-arg "SAMBA_SHARE=$SAMBA_SHARE_EFF" \
	--build-arg "SAMBA_USER=$SAMBA_USER_EFF" \
	--build-arg "SAMBA_PASS=$SAMBA_PASS_EFF" \
	diskimage

# --- 2. Export the rootfs tar ---------------------------------------------
echo "==> Exporting rootfs"
CID=$(docker create --name webvm-guest-export "$IMAGE_TAG" 2>/dev/null || {
	docker rm -f webvm-guest-export >/dev/null 2>&1
	docker create --name webvm-guest-export "$IMAGE_TAG"
})
docker export "$CID" > rootfs.tar
docker rm -f webvm-guest-export >/dev/null

# --- 3. Build the ext2 helper image (e2fsprogs, cached) --------------------
# Skip the (idempotent) build when the tag already exists: the image is
# pinned by content (ubuntu:26.04 + e2fsprogs) and `docker build` of an
# unchanged Dockerfile is pure overhead per `make build`. A stale image under
# the same tag (built from an older Dockerfile) would silently be reused, so
# pin the tag to a hash of the Dockerfile below: the tag changes whenever the
# helper definition changes, forcing a rebuild, and the skip only matches the
# current content.
HELPER_TAG="webvm-ext2-helper:$(printf '%s' 'ubuntu:26.04 + e2fsprogs' | cksum | cut -d' ' -f1)"
echo "==> Preparing ext2 helper ($HELPER_TAG)"
if ! docker image inspect "$HELPER_TAG" >/dev/null 2>&1; then
	docker build -t "$HELPER_TAG" - >/dev/null <<'EOF'
FROM ubuntu:26.04
RUN apt-get update && apt-get install -y --no-install-recommends e2fsprogs && rm -rf /var/lib/apt/lists/*
EOF
fi

# --- 4. Untar + mkfs.ext2 -d on a container-local path ---------------------
# Extraction dir is container-local /tmp/rootfs on macOS (the mounted /work is
# a macOS dir that remaps uids) or the host-mounted /work/.rootfs on Linux
# (uid-safe there, and immune to container /tmp truncation). size = rootfs +
# ~20% headroom, min 100 MiB, max 2 GiB.
echo "==> Creating ext2 image"
docker run --rm -v "$PWD":/work "$HELPER_TAG" sh -c '
	set -eu
	exroot='"$EXTRACT_DIR"'
	# The macOS case unpacks into container-local /tmp/rootfs; Linux unpacks
	# into /work/.rootfs (host-mounted, uid-safe, no container /tmp surprises).
	# The tree is root-owned on the host mount (the container extracts as
	# root), so it is removed HERE, as root, at the end of this script — never
	# by the host user (who cannot delete root-owned files).
	rm -rf "$exroot" && mkdir -p "$exroot"
	tar -xf /work/rootfs.tar -C "$exroot"
	# CRITICAL FIX (2026-08-10, root cause of the display bug): the Docker
	# daemon creates `/.dockerenv` in the container at start, so the export
	# tarball always carries it. OpenRC reads `/.dockerenv` to detect a docker
	# container and then SKIPS all `keyword -containers` services — including
	# udev/udev-trigger/udev-settle. Without udevd, X never registers the
	# virtual input devices, the cursor freezes, and the CheerpX runtime stops
	# presenting after the initial frame. Remove it from the guest rootfs here
	# (a Dockerfile `RUN rm` cannot help: it is recreated at container start).
	rm -f "$exroot/.dockerenv" "$exroot/.dockerinit"
	# Fail loudly if the extraction produced nothing: a truncated rootfs.tar or
	# a constrained /tmp would otherwise silently under-size the filesystem and
	# fail far more confusingly inside mkfs.ext2 (see EXTRACT_DIR comment).
	[ -d "$exroot/etc" ] || { echo "ERROR: rootfs unpack is empty/truncated at $exroot" >&2; exit 1; }
	# Size the ext2 from the MAX of the rootfs'"'"'s ALLOCATED and APPARENT
	# size. `du -sk` measures allocated blocks, which under-report whenever
	# the backing store keeps files smaller than their logical size — sparse
	# files (some overlay2/backing-fs combos) OR transparent compression (ZFS
	# lz4: a ~150 MiB rootfs measured ~6.6 MiB allocated on webvm@milla) — but
	# `mkfs.ext2 -d` writes every file by its APPARENT size, so a size derived
	# from allocated-only blocks can be far too small and exhaust the block
	# bitmap mid-population ("Could not allocate block in ext2 filesystem
	# while writing file ..."). `du -sk --apparent-size` is always >=
	# allocated for non-sparse trees (block rounding) and catches both cases,
	# so max() covers them all.
	rootfs_kb=$(du -sk "$exroot" | cut -f1)
	rootfs_apparent_kb=$(du -sk --apparent-size "$exroot" | cut -f1)
	[ "$rootfs_apparent_kb" -gt "$rootfs_kb" ] && rootfs_kb=$rootfs_apparent_kb
	size_mb=$(( (rootfs_kb * 12 / 10240) + 1 ))   # rootfs + ~20%
	if [ "$size_mb" -lt 100 ]; then size_mb=100; fi
	if [ "$size_mb" -gt 2000 ]; then size_mb=2000; fi
	echo "rootfs ~${rootfs_kb} KiB -> ext2 ${size_mb} MiB"
	rm -f /work/image.ext2
	mkfs.ext2 -q -m 0 -b 4096 -d "$exroot" "/work/image.ext2" "${size_mb}M"
	e2fsck -f -y "/work/image.ext2" >/dev/null
	# Ownership sanity checks (uid/gid must survive the untar on the local
	# path; debugfs prints "User:"/"Group:")
	debugfs -R "stat /home/user" "/work/image.ext2" 2>/dev/null | grep -Eq "User:[[:space:]]+1000" || { echo "ERROR: /home/user uid is not 1000" >&2; exit 1; }
	debugfs -R "stat /home/user" "/work/image.ext2" 2>/dev/null | grep -Eq "Group:[[:space:]]+1000" || { echo "ERROR: /home/user gid is not 1000" >&2; exit 1; }
	debugfs -R "stat /sbin/init" "/work/image.ext2" >/dev/null 2>&1 || { echo "ERROR: /sbin/init missing" >&2; exit 1; }
	# Remove the extraction tree AS ROOT (we are root here) so nothing
	# root-owned is left behind on the host bind mount for the host user.
	rm -rf "$exroot"
'

mv image.ext2 "$OUT_IMAGE"
rm -f rootfs.tar

# --- 4b. Warn about weak sync credentials (samba/webdav) ---------------------
# The baked /root/.syncrc (and the fingerprint below) expose credential-derived
# values to every LAN client that can reach the site; the default 'changeme' or
# any short password is trivially brute-forceable. Warn (non-fatal: CI builds
# the samba/webdav matrix with placeholder creds by design).
if [ "$STORAGE_BACKEND" = "samba" ]; then
	if [ "$SAMBA_PASS_EFF" = "changeme" ] || [ "${#SAMBA_PASS_EFF}" -lt 8 ]; then
		echo "WARNING: samba build uses a weak SAMBA_PASS ('changeme' or < 8 chars)." >&2
		echo "         It is baked into the served ext2 and feeds the served fingerprint —" >&2
		echo "         set a strong SAMBA_PASS in .env before deploying." >&2
	fi
elif [ "$STORAGE_BACKEND" = "webdav" ]; then
	if [ "$SYNC_PASS_EFF" = "changeme" ] || [ "${#SYNC_PASS_EFF}" -lt 8 ]; then
		echo "WARNING: webdav build uses a weak SYNC_PASS ('changeme' or < 8 chars)." >&2
		echo "         It is baked into the served ext2 and feeds the served fingerprint —" >&2
		echo "         set a strong SYNC_PASS in .env before deploying." >&2
	fi
fi

# --- 5. Content-stable fingerprint (cacheId suffix, NOT the ext2 bytes) ----
# Deterministic inputs: the diskimage tree + STORAGE_BACKEND + sync args
# (INCLUDING the passwords — a credential change rebuilds a different base
# image, so it must also start a fresh browser overlay; a fingerprint that
# omitted them would let stale IndexedDB deltas apply to a changed base).
# NOTE: because the sync passwords feed this digest and the digest is served
# at /custom-disk-images/image-build.txt (and embedded in the cacheId), it is
# a credential VERIFIER — LAN clients can brute-force low-entropy passwords
# against it. The same plaintext passwords are already baked into the served
# ext2's /root/.syncrc, so this is defense-in-depth, not a new primary leak;
# still, never deploy samba/webdav with the default passwords.
# __pycache__ bytecode is excluded (non-deterministic across Python versions),
# as are .DS_Store files (macOS working trees would otherwise skew the digest
# vs Linux/CI for the same commit). `trace/` is NOT fingerprinted as a whole
# directory: only trace/libtcl8.6.so.patched is COPY'd into the image, so the
# dir's stale pcmanfm-era probe sources are catted explicitly instead (the
# patched lib changes the image; touching the probes must not churn the
# cacheId of a byte-identical ext2).
FINGERPRINT_INPUT=$( \
	cat diskimage/Dockerfile; \
	# The CheerpX fix shim source is COPY'd into the image via the Dockerfile
	# build stage — its content must churn the cacheId like any other image
	# input (a changed shim with an unchanged Dockerfile would otherwise let
	# stale IndexedDB overlays serve the old guest).
	cat diskimage/faccessat-fix.c 2>/dev/null; \
	find diskimage/rootfs diskimage/config diskimage/scripts diskimage/sync \
		diskimage/examples \
		-type f -not -path '*/.git/*' -not -name '*.pyc' -not -path '*/__pycache__/*' \
		-not -name '.DS_Store' -not -path '*/rootfs/home/user/.ssh/*' -print0 2>/dev/null | sort -z | xargs -0 cat; \
	cat diskimage/trace/libtcl8.6.so.patched 2>/dev/null; \
	echo "$STORAGE_BACKEND"; \
	echo "SYNC_URL=$SYNC_URL_EFF SYNC_USER=$SYNC_USER_EFF SYNC_PASS=$SYNC_PASS_EFF"; \
	echo "SAMBA_HOST=$SAMBA_HOST_EFF SAMBA_SHARE=$SAMBA_SHARE_EFF SAMBA_USER=$SAMBA_USER_EFF SAMBA_PASS=$SAMBA_PASS_EFF" \
)
FINGERPRINT=$(printf '%s' "$FINGERPRINT_INPUT" | shasum -a 256 | cut -c1-12)

echo "$FINGERPRINT" > "$FINGERPRINT_FILE"

# Record the backend the image was built for, alongside the fingerprint: the
# deploy targets (make up/up-tailnet) compare it against the .env mode and
# refuse to start a mismatched stack (2026-08-15 fix).
echo "$STORAGE_BACKEND" > "$OUT_DIR/image-backend.txt"

echo ""
echo "==> Done"
echo "   backend:     $STORAGE_BACKEND"
echo "   ext2:        $OUT_IMAGE ($(du -h "$OUT_IMAGE" | cut -f1))"
echo "   fingerprint: $FINGERPRINT  (cacheId = ${CACHE_ID_PREFIX}$FINGERPRINT)"
echo ""
echo "Next: cd webvm && WEBVM_MODE=$STORAGE_BACKEND WEBVM_IMAGE_BUILD=$FINGERPRINT npm run build"
