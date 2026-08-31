#!/bin/sh
# Generate the private CA and the site/control/DERP server certificate.
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
# (https://<LAN_IP>:<SITE_PORT>). There is no plain-HTTP access path.
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

CERT_DIR="${CERT_DIR:-./certs}"
CERT_DAYS="${CERT_DAYS:-3650}"

mkdir -p "$CERT_DIR"

# --- Private CA (generated once; reused across runs) ---
if [ ! -f "$CERT_DIR/ca.key" ] || [ ! -f "$CERT_DIR/ca.crt" ]; then
	openssl genrsa -out "$CERT_DIR/ca.key" 4096
	openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" -sha256 -days "$CERT_DAYS" \
		-subj "/CN=webvm-custom Private CA" -out "$CERT_DIR/ca.crt"
fi

# --- Server certificate (regenerated on each run so SAN always matches env) ---
# DNS SAN entries only for actual hostnames. CONTROL_HOST is an IP literal
# (127.0.0.1 single machine / the hardcoded LAN IP) in every supported
# deployment — an IP-in-DNS entry is invalid and browsers ignore it (the
# IP: entries cover it). localhost is the one genuine hostname.
#
# is_ip_literal: an IP literal must contain a '.' (IPv4) or ':' (IPv6).
# A bare dotless/colonless token (e.g. a hostname like "dead" or "babe",
# which is entirely hex digits) is a HOSTNAME and must get a DNS: entry —
# the old hex/colon/dot character-class test misclassified such hostnames
# as IPs and silently dropped their DNS SAN (TLS hostname mismatch).
is_ip_literal() {
	case "$1" in
		*.* | *:*) return 0 ;;
	esac
	return 1
}
SAN="DNS:localhost,IP:127.0.0.1,IP:${LAN_IP},IP:${GATEWAY_CONTROL_IP}"
if ! is_ip_literal "$CONTROL_HOST"; then
	SAN="DNS:${CONTROL_HOST},${SAN}"
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
