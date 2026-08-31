#!/bin/sh
# Manual / LAN acceptance checklist (plan §10) — run on the LAN host after
# `make up` (or `make up-tailnet`). Covers what CI cannot: private-CA browser
# trust, socat relay reachability from inside the guest, Samba connect, the
# no-internet proofs, and the visual items.
set -eu

# Shared defaults + .env loader + helpers: the deployment values (and the
# samba/webdav credentials used in the printed commands below) come from
# .env / the environment — never empty defaults.
WEBVM_COMMON="${WEBVM_COMMON:-$(dirname "$0")/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"
webvm_load_dotenv

SITE_URL="https://${LAN_IP}:${SITE_PORT}"
# The desktop page route + image dir/name come from the shared lib (the same
# values nginx serves and build.sh produces).
PAGE_URL="$SITE_URL/$ALPINE_PAGE"
IMAGE_URL="$SITE_URL/$WEBVM_IMAGE_DIR/$WEBVM_IMAGE_NAME"

echo "== WebVM LAN acceptance ($STORAGE_BACKEND) =="

echo ""
echo "[1] Site access + headers"
curl -sk -o /dev/null -w "  $ALPINE_PAGE -> %{http_code}\n" "$PAGE_URL"
curl -sk -o /dev/null -w "  /            -> %{http_code} (expect 302)\n" "$SITE_URL/"
curl -sk -D - -o /dev/null "$PAGE_URL" | grep -qi "cross-origin-embedder-policy: require-corp" \
	&& echo "  COEP require-corp: OK" || echo "  COEP: MISSING"
curl -sk -H "Range: bytes=0-1023" -o /dev/null -w "  ext2 Range    -> %{http_code} (expect 206)\n" \
	"$IMAGE_URL"

echo ""
echo "[2] No-internet proofs (manual, in the browser DevTools Network tab):"
echo "  * Open $PAGE_URL"
echo "  * Expected requests: SAME-ORIGIN ONLY (page, assets, the ext2,"
echo "    /$WEBVM_IMAGE_DIR/…) — zero external hosts."
echo "  * In network modes the only cross-origin traffic is to"
echo "    ${CONTROL_HOST}:${CONTROL_PORT} (WSS control + /derp)."
echo "  * The blocked logtail fetch (log.tailscale.com) may appear as a CSP"
echo "    warning in the console — that is EXPECTED (blocked, not permitted)."
echo "  * Never open a URL with #authKey but no #controlUrl (that would"
echo "    auto-register with PUBLIC Tailscale)."

echo ""
echo "[3] Guest-side reachability (from the xterm in the guest):"
case "$STORAGE_BACKEND" in
	samba)
		echo "  nc -z $GATEWAY_TAILNET_IP 445       -> must succeed (samba relay)"
		echo "  nc -z <raw-samba-LAN-IP> 445        -> must FAIL (no subnet routes)"
		;;
	webdav)
		echo "  nc -z $GATEWAY_TAILNET_IP $WEBDAV_PORT -> must succeed (webdav relay)"
		echo "  nc -z <raw-LAN-IP> $WEBDAV_PORT      -> must FAIL (no subnet routes)"
		;;
esac
echo "  nc -z 1.1.1.1 443                 -> must FAIL (no internet, no exit node)"
echo "  ip route                           -> must show NO default route"

echo ""
echo "[4] TLS/control (network modes)"
echo "  * Networking tab shows CONNECTED with a tailnet IP over WSS."
echo "  * docker compose exec server headscale nodes list  -> browser + gateway nodes."

echo ""
echo "[5] Storage sync (per backend) — manual:"
case "$STORAGE_BACKEND" in
	browser)
		echo "  * Write a file in ~/ in the guest; reload the tab; file survives."
		echo "  * Two tabs: the second shows the single-session notice + ephemeral boot."
		;;
	none)
		echo "  * Write a file in ~/ in the guest; reload the tab; file is GONE."
		;;
	samba)
		echo "  * In the guest: smbclient //$GATEWAY_TAILNET_IP/$SAMBA_SHARE -U $SAMBA_USER"
		echo "    (baked /home/user/.syncrc) — push a file, verify on the LAN Samba server."
		echo "  * Save a file in IDLE -> pushed within a few seconds; reboot tab -> pulled back."
		;;
	webdav)
		echo "  * Host check: curl -u $WEBDAV_USER:$WEBDAV_PASS -X PROPFIND -H 'Depth: 1'"
		echo "    http://$LAN_IP:$WEBDAV_PORT/webdav/  (PROPFIND must list files)"
		echo "  * Save a file in IDLE -> pushed within a few seconds to \$WEBDAV_ROOT;"
		echo "    reboot tab -> pulled back."
		;;
esac

echo ""
echo "[6] Git (when a LAN git server is wanted)"
echo "  Host step:  GIT_SSH_LAN_IP=<git-server> in .env, then"
echo "              docker compose --profile tailnet up -d"
echo "  Guest step: git remote add origin ssh://git@$GATEWAY_TAILNET_IP:$GIT_SSH_PORT/<path>"
echo "              then git clone / pull / push against the LAN remote."

echo ""
echo "[7] Image size / first load"
ls -lh "webvm/$WEBVM_IMAGE_DIR/$WEBVM_IMAGE_NAME" 2>/dev/null | awk '{print "  ext2:", $5}'
echo "  * Record the first-load time in the browser (must be < 2 GiB image)."

echo ""
echo "[8] Port remapping (no rebuild)"
echo "  * Change SITE_PORT/CONTROL_PORT/WEBDAV_PORT/STUN_PORT in compose.yaml (or .env),"
echo "    docker compose --profile tailnet up -d — the URL hash, syncrc and gateway"
echo "    relays follow the same env vars."

echo ""
echo "[9] Paste from the device (manual, plans/clipboard-paste.md)"
echo "  * Open the sidebar Clipboard panel; type a few lines (ASCII) and click"
echo "    Paste — the text must appear in the FOCUSED guest window (xterm or"
echo "    the file explorer's Search box) as if typed by hand."
echo "  * Paste 'café — 日本語' — must be refused with 'cannot be typed as"
echo "    keys: char U+00E9 …' and nothing sent."
echo "  * Type 400+ chars — the panel must show the '~2s to type' warning"
echo "    (5 ms/char since 2026-08-29 — the estimate lives in"
echo "    webvm/src/lib/clipboard.js CX_TYPE_DELAY_MS)."
echo "  * Open file… / drag a .txt onto the box — content loads and pastes."
echo "  * In-VM Ctrl+C / Ctrl+V inside IDLE must still work natively."

echo ""
echo "== Acceptance checks listed. Visual items (desktop renders, IDLE usable,"
echo "   canvas resize) require a human in the browser. =="
