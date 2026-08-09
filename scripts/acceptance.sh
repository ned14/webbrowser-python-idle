#!/bin/sh
# Manual / LAN acceptance checklist (plan §10) — run on the LAN host after
# `make up` (or `make up-tailnet`). Covers what CI cannot: private-CA browser
# trust, socat relay reachability from inside the guest, Samba connect, the
# no-internet proofs, and the visual items.
set -eu

STORAGE_BACKEND="${STORAGE_BACKEND:-browser}"
CONTROL_HOST="${CONTROL_HOST:-host.docker.internal}"
LAN_IP="${LAN_IP:-127.0.0.1}"
SITE_PORT="${SITE_PORT:-8081}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
GATEWAY_TAILNET_IP="${GATEWAY_TAILNET_IP:-}"

SITE_URL="https://${LAN_IP}:${SITE_PORT}"

echo "== WebVM LAN acceptance ($STORAGE_BACKEND) =="

echo ""
echo "[1] Site access + headers"
curl -sk -o /dev/null -w "  /alpine.html -> %{http_code}\n" "$SITE_URL/alpine.html"
curl -sk -o /dev/null -w "  /            -> %{http_code} (expect 302)\n" "$SITE_URL/"
curl -sk -D - -o /dev/null "$SITE_URL/alpine.html" | grep -qi "cross-origin-embedder-policy: require-corp" \
	&& echo "  COEP require-corp: OK" || echo "  COEP: MISSING"
curl -sk -H "Range: bytes=0-1023" -o /dev/null -w "  ext2 Range    -> %{http_code} (expect 206)\n" \
	"$SITE_URL/custom-disk-images/webvm-custom-disk.ext2"

echo ""
echo "[2] No-internet proofs (manual, in the browser DevTools Network tab):"
echo "  * Open $SITE_URL/alpine.html"
echo "  * Expected requests: SAME-ORIGIN ONLY (page, assets, the ext2,"
echo "    /custom-disk-images/…) — zero external hosts."
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
echo "  Guest step: git remote add origin ssh://git@$GATEWAY_TAILNET_IP:2222/<path>"
echo "              then git clone / pull / push against the LAN remote."

echo ""
echo "[7] Image size / first load"
ls -lh webvm/custom-disk-images/webvm-custom-disk.ext2 2>/dev/null | awk '{print "  ext2:", $5}'
echo "  * Record the first-load time in the browser (must be < 2 GiB image)."

echo ""
echo "[8] Port remapping (no rebuild)"
echo "  * Change SITE_PORT/CONTROL_PORT/WEBDAV_PORT/STUN_PORT in compose.yaml (or .env),"
echo "    docker compose --profile tailnet up -d — the URL hash, syncrc and gateway"
echo "    relays follow the same env vars."

echo ""
echo "== Acceptance checks listed. Visual items (desktop renders, IDLE usable,"
echo "   canvas resize) require a human in the browser. =="
