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

STORAGE_BACKEND="${1:-${STORAGE_BACKEND:-browser}}"
case "$STORAGE_BACKEND" in
	browser|samba|webdav|none) ;;
	*) echo "Unknown STORAGE_BACKEND: $STORAGE_BACKEND (browser|samba|webdav|none)" >&2; exit 1 ;;
esac

IMAGE_TAG="webvm-guest"
HELPER_TAG="webvm-ext2-helper"
OUT_DIR="webvm/custom-disk-images"
OUT_IMAGE="$OUT_DIR/webvm-custom-disk.ext2"
FINGERPRINT_FILE="$OUT_DIR/image-build.txt"

# Clean up the credential-bearing export tarball and the export container even
# when a later step fails (set -eu would otherwise leave rootfs.tar behind).
cleanup() {
	docker rm -f webvm-guest-export >/dev/null 2>&1 || true
	rm -f rootfs.tar
}
trap cleanup EXIT

# Effective build args (same defaults as diskimage/Dockerfile), reused by the
# fingerprint so CI and local builds agree on content-identical images.
SYNC_URL_EFF="${SYNC_URL:-http://100.64.0.1:8082/webdav/}"
SYNC_USER_EFF="${SYNC_USER:-webdav}"
SYNC_PASS_EFF="${SYNC_PASS:-changeme}"
SAMBA_HOST_EFF="${SAMBA_HOST:-${GATEWAY_TAILNET_IP:-100.64.0.1}}"
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
echo "==> Preparing ext2 helper"
docker build -t "$HELPER_TAG" - >/dev/null <<'EOF'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends e2fsprogs && rm -rf /var/lib/apt/lists/*
EOF

# --- 4. Untar + mkfs.ext2 -d on a container-local path ---------------------
# size = rootfs + ~20% headroom, min 100 MiB, max 2 GiB.
echo "==> Creating ext2 image"
docker run --rm -v "$PWD":/work "$HELPER_TAG" sh -c '
	set -eu
	rm -rf /tmp/rootfs && mkdir -p /tmp/rootfs
	tar -xf /work/rootfs.tar -C /tmp/rootfs
	rootfs_kb=$(du -sk /tmp/rootfs | cut -f1)
	size_mb=$(( (rootfs_kb * 12 / 10240) + 1 ))   # rootfs + ~20%
	if [ "$size_mb" -lt 100 ]; then size_mb=100; fi
	if [ "$size_mb" -gt 2000 ]; then size_mb=2000; fi
	echo "rootfs ~${rootfs_kb} KiB -> ext2 ${size_mb} MiB"
	rm -f /work/image.ext2
	mkfs.ext2 -q -m 0 -b 4096 -d /tmp/rootfs "/work/image.ext2" "${size_mb}M"
	e2fsck -f -y "/work/image.ext2" >/dev/null
	# Ownership sanity checks (uid/gid must survive the untar on the local
	# path; debugfs prints "User:"/"Group:")
	debugfs -R "stat /home/user" "/work/image.ext2" 2>/dev/null | grep -Eq "User:[[:space:]]+1000" || { echo "ERROR: /home/user uid is not 1000" >&2; exit 1; }
	debugfs -R "stat /home/user" "/work/image.ext2" 2>/dev/null | grep -Eq "Group:[[:space:]]+1000" || { echo "ERROR: /home/user gid is not 1000" >&2; exit 1; }
	debugfs -R "stat /sbin/init" "/work/image.ext2" >/dev/null 2>&1 || { echo "ERROR: /sbin/init missing" >&2; exit 1; }
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
# __pycache__ bytecode is excluded (non-deterministic across Python versions).
FINGERPRINT_INPUT=$( \
	cat diskimage/Dockerfile; \
	find diskimage/rootfs diskimage/config diskimage/scripts diskimage/sync \
		-type f -not -path '*/.git/*' -not -name '*.pyc' -not -path '*/__pycache__/*' \
		-not -path '*/rootfs/home/user/.ssh/*' -print 2>/dev/null | sort | xargs cat; \
	echo "$STORAGE_BACKEND"; \
	echo "SYNC_URL=$SYNC_URL_EFF SYNC_USER=$SYNC_USER_EFF SYNC_PASS=$SYNC_PASS_EFF"; \
	echo "SAMBA_HOST=$SAMBA_HOST_EFF SAMBA_SHARE=$SAMBA_SHARE_EFF SAMBA_USER=$SAMBA_USER_EFF SAMBA_PASS=$SAMBA_PASS_EFF" \
)
FINGERPRINT=$(printf '%s' "$FINGERPRINT_INPUT" | shasum -a 256 | cut -c1-12)

echo "$FINGERPRINT" > "$FINGERPRINT_FILE"

echo ""
echo "==> Done"
echo "   ext2:        $OUT_IMAGE ($(du -h "$OUT_IMAGE" | cut -f1))"
echo "   fingerprint: $FINGERPRINT  (cacheId = blocks_alpine_$FINGERPRINT)"
echo ""
echo "Next: cd webvm && WEBVM_MODE=$STORAGE_BACKEND WEBVM_IMAGE_BUILD=$FINGERPRINT npm run build"
