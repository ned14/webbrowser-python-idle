#!/bin/sh
# Generate the private CA and the site/control/DERP server certificate — or,
# when the OPT-IN Let's Encrypt facility is enabled (LETSENCRYPT_EMAIL set in
# .env), obtain/renew a PUBLIC cert for the DNS-named deployment instead
# (certbot HTTP-01 standalone; see the LETSENCRYPT section below). The
# private-CA path is byte-unchanged when the facility is off.
#
# The certificate's SAN covers CONTROL_HOST (default 127.0.0.1 — the
# browser-facing control host), 127.0.0.1, localhost, the LAN IP and the
# server container's static compose-network IP (GATEWAY_CONTROL_IP,
# 172.28.0.10, used by the gateway and the integration join-test client).
# HOSTNAMES ARE BANNED (no host.docker.internal / /etc/hosts tricks — never
# reintroduce).
#
# The CA must be installed and trusted in the browser once — for both the
# single-machine path (https://127.0.0.1:<SITE_PORT>) and LAN use
# (https://<LAN_IP>:<SITE_PORT>). There is no plain-HTTP serving path (the LE
# facility binds port 80 only while certbot validates, and only when enabled).
set -eu

# Shared defaults + helpers (CONTROL_HOST/LAN_IP/GATEWAY_CONTROL_IP come from
# the lib; the gateway and the integration join-test client use the SAME
# GATEWAY_CONTROL_IP value — see compose.yaml).
WEBVM_COMMON="${WEBVM_COMMON:-$(dirname "$0")/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"

# .env overrides reach the script ONLY when loaded here: `make certs`/`make
# up-tailnet` pass no CONTROL_HOST/LAN_IP on the command line, so without this
# the cert SAN would silently cover the defaults (127.0.0.1) instead of the
# deployment's CONTROL_HOST — a LAN/public host that TLS then rejects. Explicit
# env vars (command line / CI) still win: webvm_load_dotenv never overrides
# them, with the same precedence as every other consumer.
webvm_load_dotenv

CERT_DIR="${CERT_DIR:-./certs}"
CERT_DAYS="${CERT_DAYS:-3650}"

mkdir -p "$CERT_DIR"

# is_ip_literal: a REAL IP literal must be an IPv6 (contains ':') or exactly
# four dot-separated DECIMAL octets (IPv4). Anything else — including a dotted
# DNS name such as webvm.nedprod.com, and the bare hex-ish tokens 'dead'/'babe'
# the old character-class test misclassified — is a HOSTNAME and gets a DNS:
# entry. (The previous '. or :' test treated every dotted token as an IP, which
# silently dropped the DNS SAN of dotted control hosts.)
is_ip_literal() {
	case "$1" in
		*:*) return 0 ;;
	esac
	_rest=$1
	_i=0
	while :; do
		_oct=${_rest%%.*}
		case "$_oct" in
			''|*[!0-9]*) return 1 ;;
		esac
		_i=$((_i + 1))
		[ "$_rest" = "$_oct" ] && break
		[ "$_i" -ge 4 ] && return 1
		_rest=${_rest#*.}
	done
	[ "$_i" -eq 4 ]
}

# webvm_disk_host: prints the HOSTNAME part of WEBVM_DISK_BASE_URL — '' when
# unset/empty or an IP literal (IPs never get DNS: SAN entries; the IP: entries
# of the private cert cover a same-box disk host, and LE has no IP SANs at
# all). The single home of the disk-host extraction: the private SAN, its
# coverage-skip and the LE domain derivation all consume this one function.
webvm_disk_host() {
	_url=${WEBVM_DISK_BASE_URL:-}
	[ -n "$_url" ] || return 0
	case "$_url" in
		*://*) _url=${_url#*://} ;;
	esac
	_url=${_url%%[/:]*}
	case "$_url" in
		''|localhost) return 0 ;;
	esac
	if is_ip_literal "$_url"; then
		return 0
	fi
	printf '%s\n' "$_url"
}

# --- Private CA (generated once; reused across runs) ---
if [ ! -f "$CERT_DIR/ca.key" ] || [ ! -f "$CERT_DIR/ca.crt" ]; then
	openssl genrsa -out "$CERT_DIR/ca.key" 4096
	openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" -sha256 -days "$CERT_DAYS" \
		-subj "/CN=webvm-custom Private CA" -out "$CERT_DIR/ca.crt"
fi

# --- Public Let's Encrypt cert (OPT-IN facility: LETSENCRYPT_EMAIL set) ------
# Replaces the private server cert with a PUBLIC Let's Encrypt cert — for
# deployments whose browsers must trust the origin WITHOUT installing the
# private CA (e.g. a proxied-off disk host such as disk.webvm.nedprod.com that
# public visitors read the ext2 from). The facility is host-side only: nginx
# keeps reading certs/server.{crt,key} (unchanged mount), so no compose/template
# change exists. Enabled via .env (LETSENCRYPT_EMAIL; optional
# LETSENCRYPT_DOMAINS override) — see scripts/lib/webvm-common.sh + .env.example.
#
# Challenge: certbot HTTP-01 STANDALONE on port 80. Every SAN domain must
# therefore resolve DIRECTLY to this box (DNS-only records, no CDN proxy and
# no Always-HTTPS redirect in front): a proxied hostname like webvm.nedprod.com
# behind Cloudflare cannot be validated this way and must NOT be listed.
# Port 80 is bound only while certbot runs (nothing else in this stack listens
# on it — compose publishes 443/8443/… only), so the repo's no-plain-HTTP
# serving path is untouched.
#
# Renewal: certbot's own schedule (the distro's certbot.timer / cron) re-runs
# the recorded authenticator and deploy hook. The deploy hook AND every
# gen-certs.sh run then execute scripts/le-install.sh, which copies the
# refreshed cert into certs/ and reloads nginx when the stack is running.
#
# NOT for tailnet/LAN deployments: the public cert has NO IP SANs (the gateway
# dials the control plane at 172.28.0.10) and is not signed by the private CA
# (the gateway's SSL_CERT_FILE=/certs/ca.crt trust) — enabling it there fails
# closed below instead of breaking the gateway's TLS silently.
if [ -n "${LETSENCRYPT_EMAIL:-}" ]; then
	# Fail closed when the deployment needs the control plane (samba/webdav
	# backends, or browser/none with HEADSCALE_ENABLED=1 — the shared matrix).
	if webvm_backend_needs_headscale "$STORAGE_BACKEND"; then
		echo "FATAL: LETSENCRYPT_EMAIL is set but STORAGE_BACKEND=$STORAGE_BACKEND needs the control plane (gateway/headscale)." >&2
		echo "       The Let's Encrypt cert has no IP SANs (the gateway dials ${GATEWAY_CONTROL_IP}) and is not signed by certs/ca.crt" >&2
		echo "       (the gateway's SSL_CERT_FILE trust) — the tailnet would break. Unset LETSENCRYPT_EMAIL for tailnet modes." >&2
		exit 1
	fi

	# SAN domains: explicit override (LETSENCRYPT_DOMAINS) or derived from the
	# DNS names the private cert would carry (a hostname CONTROL_HOST + the
	# WEBVM_DISK_BASE_URL host) — never IP literals, never localhost.
	_LE_LIST=""
	if [ -n "${LETSENCRYPT_DOMAINS:-}" ]; then
		_LE_LIST=$(printf '%s' "$LETSENCRYPT_DOMAINS" | tr ',' ' ')
	else
		if ! is_ip_literal "$CONTROL_HOST"; then
			_LE_LIST="$CONTROL_HOST"
		fi
		_le_disk=$(webvm_disk_host)
		[ -n "$_le_disk" ] && _LE_LIST="$_LE_LIST $_le_disk"
	fi
	_LE_SAN=""
	for _d in $_LE_LIST; do
		case "$_d" in
			'') continue ;;
		esac
		if is_ip_literal "$_d" || [ "$_d" = "localhost" ]; then
			echo "FATAL: Let's Encrypt SAN entry '$_d' is not a public DNS name (IP literals and localhost are not issuable)." >&2
			echo "       Set LETSENCRYPT_DOMAINS to the public hostname(s) that resolve directly to this box." >&2
			exit 1
		fi
		# Dedupe (keep the first occurrence).
		case " $_LE_SAN " in
			*" $_d "*) continue ;;
		esac
		_LE_SAN="$_LE_SAN $_d"
	done
	if [ -z "$_LE_SAN" ]; then
		echo "FATAL: LETSENCRYPT_EMAIL is set but there is no public DNS name to certify." >&2
		echo "       Set LETSENCRYPT_DOMAINS (e.g. disk.example.com), or point CONTROL_HOST at a hostname / set" >&2
		echo "       WEBVM_DISK_BASE_URL at a hostname to derive one. IP-only deployments keep the private CA." >&2
		exit 1
	fi

	# certbot is a HOST package (this script runs on the host, never in a
	# container image). Fail closed with the install hint instead of a cryptic
	# command-not-found.
	if ! command -v certbot >/dev/null 2>&1; then
		echo "FATAL: certbot not found (required by the LETSENCRYPT_EMAIL facility)." >&2
		echo "       Debian/Ubuntu: apt-get install -y certbot   (the certbot.timer then renews twice daily)" >&2
		echo "       Other distros: install the distro certbot package, or unset LETSENCRYPT_EMAIL for the private CA." >&2
		exit 1
	fi

	# le-install.sh (the cert->certs/ install + nginx reload) is both the
	# certbot deploy hook and the always-run copy below (a fresh checkout has
	# no certs/ yet while certbot's lineage already exists). Absolute path:
	# certbot runs deploy hooks with an arbitrary cwd (systemd timer = /).
	_LE_INSTALL="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)/le-install.sh"

	# The lineage (LETSENCRYPT_CERT_NAME) is STABLE: certbot skips the issue
	# when the existing cert is valid and covers the same domains (no CA
	# contact, ~1 s), so `make up` (which runs this every launch) stays cheap.
	echo "==> LETSENCRYPT facility on (email=$LETSENCRYPT_EMAIL): ensuring a public cert for:${_LE_SAN}"
	set -- certbot certonly --non-interactive --agree-tos --email "$LETSENCRYPT_EMAIL" \
		--cert-name "$LETSENCRYPT_CERT_NAME" --config-dir "$LETSENCRYPT_ROOT" \
		--standalone --preferred-challenges http \
		--deploy-hook "$_LE_INSTALL"
	for _d in $_LE_SAN; do
		set -- "$@" -d "$_d"
	done
	"$@"

	# Install the (possibly pre-existing, not-yet-reissued) cert into certs/
	# and reload nginx when the stack is running (the deploy hook above only
	# fires on an actual issue/renewal).
	"$_LE_INSTALL"

	echo ""
	echo "Let's Encrypt cert installed for:${_LE_SAN}"
	echo "  server.crt / server.key now serve the PUBLIC cert (no certs/ca.crt trust needed for these names)"
	echo "  Renewal: certbot's own schedule re-issues before expiry and le-install.sh swaps + reloads nginx"
	exit 0
fi

# --- Server certificate (regenerated on each run so SAN always matches env) ---
# DNS SAN entries only for actual hostnames. CONTROL_HOST is an IP literal
# (127.0.0.1 single machine / the hardcoded LAN IP) in every supported
# deployment — an IP-in-DNS entry is invalid and browsers ignore it (the
# IP: entries cover it). localhost is the one genuine hostname.
SAN="DNS:localhost,IP:127.0.0.1,IP:${LAN_IP},IP:${GATEWAY_CONTROL_IP}"
if ! is_ip_literal "$CONTROL_HOST"; then
	SAN="DNS:${CONTROL_HOST},${SAN}"
fi

# Optional disk-image host (configurable facility 2026-09-02): when the
# deployment reads its ext2 cross-origin from WEBVM_DISK_BASE_URL (e.g.
# https://disk.webvm.nedprod.com -> the same origin box, proxied-off DNS),
# the server certificate must also cover that hostname or the direct TLS
# handshake fails (the browser checks the SAN of the disk origin). Only the
# hostname part is added — never a URL path/port (SAN entries are
# hostnames; the private-CA trust caveat below applies to whoever reads the
# ext2 directly). webvm_disk_host is the single extraction helper (shared
# with the Let's Encrypt domain derivation above).
_DISK_HOST=$(webvm_disk_host)
if [ -n "$_DISK_HOST" ]; then
	SAN="DNS:${_DISK_HOST},${SAN}"
fi

# Skip the server-cert regeneration when the CURRENT cert already covers the
# env (same CONTROL_HOST/LAN_IP/GATEWAY_CONTROL_IP SAN). `make up` runs this
# on every launch; regenerating a byte-different cert while a container is
# running serves the OLD mounted cert until --force-recreate, which reads as
# "my .env change did nothing". The CA is never regenerated either way.
# openssl prints the SAN as "DNS:localhost, IP Address:127.0.0.1" (possibly
# wrapped): normalize to one comma-joined token string and match the needed
# entries as a contiguous substring (extra entries are harmless).
_needed_san="DNS:localhost,IP:127.0.0.1,IP:${LAN_IP},IP:${GATEWAY_CONTROL_IP}"
if ! is_ip_literal "$CONTROL_HOST"; then
	_needed_san="DNS:${CONTROL_HOST},${_needed_san}"
fi
if [ -n "$_DISK_HOST" ]; then
	_needed_san="DNS:${_DISK_HOST},${_needed_san}"
fi
_existing_san=$(openssl x509 -in "$CERT_DIR/server.crt" -noout -ext subjectAltName 2>/dev/null \
	| sed -n '2,$p' | sed 's/IP Address:/IP:/g' | tr -d ' \t\n')
if [ -f "$CERT_DIR/server.crt" ] && [ -f "$CERT_DIR/server.key" ] && [ -n "$_existing_san" ] \
	&& case ",${_existing_san}," in *",${_needed_san},"*) true ;; *) false ;; esac; then
	echo "Server certificate already covers the current SAN (${_needed_san}) — reusing it."
else
	openssl genrsa -out "$CERT_DIR/server.key" 2048

	cat > "$CERT_DIR/server.ext" <<EOF
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${SAN}
EOF

	openssl req -new -key "$CERT_DIR/server.key" \
		-subj "/CN=${CONTROL_HOST}" -out "$CERT_DIR/server.csr"
	openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
		-CAcreateserial -days "$CERT_DAYS" -sha256 -extfile "$CERT_DIR/server.ext" \
		-out "$CERT_DIR/server.crt"

	rm -f "$CERT_DIR/server.csr" "$CERT_DIR/server.ext"
fi

echo "Certificates written to $CERT_DIR/:"
echo "  ca.crt       (trust this in your browser)"
echo "  server.crt   /  server.key"
echo ""
echo "Next steps:"
echo "  1. Trust $CERT_DIR/ca.crt in your browser (Keychain on macOS, or the"
echo "     browser's certificate store). This is required for BOTH the single-"
echo "     machine path and LAN use — HTTPS is the only access mode."
echo "  2. Open https://${LAN_IP}:${SITE_PORT:-8081}/alpine.html"
