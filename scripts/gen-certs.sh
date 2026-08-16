#!/bin/sh
# Generate the private CA and the site/control/DERP server certificate.
#
# The certificate's SAN covers CONTROL_HOST (default 127.0.0.1 — the
# browser-facing control host), 127.0.0.1, localhost, the LAN IP and the
# server container's static compose-network IP (172.28.0.10, used by the
# gateway and the integration join-test client). HOSTNAMES ARE BANNED (no
# host.docker.internal / /etc/hosts tricks — never reintroduce).
#
# The CA must be installed and trusted in the browser once — for both the
# single-machine path (https://127.0.0.1:<SITE_PORT>) and LAN use
# (https://<LAN_IP>:<SITE_PORT>). There is no plain-HTTP access path.
set -eu

CONTROL_HOST="${CONTROL_HOST:-127.0.0.1}"
LAN_IP="${LAN_IP:-127.0.0.1}"
SERVER_IP="${SERVER_IP:-172.28.0.10}"
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
openssl genrsa -out "$CERT_DIR/server.key" 2048

# DNS SAN entries only for actual hostnames. CONTROL_HOST is an IP literal
# (127.0.0.1 single machine / the hardcoded LAN IP) in every supported
# deployment — an IP-in-DNS entry is invalid and browsers ignore it (the
# IP: entries cover it). localhost is the one genuine hostname.
is_ip_literal() {
	case "$1" in
		'' | *[!0-9A-Fa-f:.]* ) return 1 ;;
	esac
	return 0
}
SAN="DNS:localhost,IP:127.0.0.1,IP:${LAN_IP},IP:${SERVER_IP}"
if ! is_ip_literal "$CONTROL_HOST"; then
	SAN="DNS:${CONTROL_HOST},${SAN}"
fi

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

echo "Certificates written to $CERT_DIR/:"
echo "  ca.crt       (trust this in your browser)"
echo "  server.crt   /  server.key"
echo ""
echo "Next steps:"
echo "  1. Trust $CERT_DIR/ca.crt in your browser (Keychain on macOS, or the"
echo "     browser's certificate store). This is required for BOTH the single-"
echo "     machine path and LAN use — HTTPS is the only access mode."
echo "  2. Open https://${LAN_IP}:${SITE_PORT:-8081}/alpine.html"
