#!/bin/sh
# Shared environment defaults + helpers for every webvm shell script.
#
# Single home for the deployment defaults (CONTROL_HOST/LAN_IP/ports) and the
# repeated patterns (wait-for-ready loops, fail-closed secret checks, the
# supervisor loop, the .env loader). Consumers source this file and then use
# the helpers; the drift unit test (tests/unit/test_scripts.py) asserts the
# defaults live HERE and that the consumers source it.
#
# In the Docker images this is COPY'd to /etc/webvm/lib/webvm-common.sh;
# in the repo it lives at scripts/lib/webvm-common.sh. Consumers may point
# WEBVM_COMMON at either location.
#
# HOSTNAMES ARE BANNED (host.docker.internal, /etc/hosts tricks — never
# reintroduce): the browser must reach the control plane over 127.0.0.1 / a
# hardcoded LAN IP alone, and the gateway reaches the server over the compose
# network at the static IP (GATEWAY_CONTROL_IP). See AGENTS.md + the plan
# §12/13/31.

# --------------------------------------------------------------------------
# Deployment defaults (idempotent: never clobber an already-set value).
# --------------------------------------------------------------------------
# Browser-facing control-plane/DERP host: default 127.0.0.1 = zero-config
# single machine; LAN deployments set it (with LAN_IP) to the hardcoded LAN
# address, e.g. 192.168.1.10.
CONTROL_HOST="${CONTROL_HOST:-127.0.0.1}"
LAN_IP="${LAN_IP:-127.0.0.1}"
SITE_PORT="${SITE_PORT:-8081}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
STUN_PORT="${STUN_PORT:-3478}"
WEBDAV_ROOT="${WEBDAV_ROOT:-/data/webdav}"
# Host-side WebDAV sync storage mount (compose `${DATA_DIR:-./data}` ->
# ${WEBDAV_ROOT}); the Makefile resolves it through this lib so the shell
# and compose cannot disagree.
DATA_DIR="${DATA_DIR:-./data}"
STORAGE_BACKEND="${STORAGE_BACKEND:-browser}"
HEADSCALE_ENABLED="${HEADSCALE_ENABLED:-0}"
HEADSCALE_BOOTSTRAP="${HEADSCALE_BOOTSTRAP:-0}"
# `make up` launches with WEBVM_TAILNET=off: a HARD-NETWORKLESS start (the
# server entrypoint then forces STORAGE_BACKEND=none and renders an empty
# baked config, whatever .env carries). `make up-tailnet` passes on (the
# default) — the ONLY tailnet-capable launch.
WEBVM_TAILNET="${WEBVM_TAILNET:-on}"
# Optional cross-origin base for the ext2 disk-image URL (configurable
# facility 2026-09-02): empty = same-origin image reads. Set it (e.g.
# https://disk.webvm.nedprod.com) to make the page read the ext2 from a
# dedicated disk host — the frontend build, the CSP connect-src, the nginx
# CORS answer and the server-cert SAN all consume this ONE value (see
# config_public_alpine.js / render-webvm-config.py / nginx.conf.template /
# gen-certs.sh; tests enforce the compose default matches this lib).
WEBVM_DISK_BASE_URL="${WEBVM_DISK_BASE_URL:-}"
# The server container's static compose-network IP — the ONLY address the
# gateway/join-test client use for the control plane (cert SAN covers it;
# the cert generator and compose/gateway/tests must agree on this value).
GATEWAY_CONTROL_IP="${GATEWAY_CONTROL_IP:-172.28.0.10}"
# The WebDAV endpoint's URL base path (mounted in wsgidav, rendered into the
# baked page config's syncUrl, baked into the guest's syncrc default, and
# exercised by the E2E probes — ONE value, this lib is the single home;
# tests/unit/test_scripts.py enforces the compose/renderer lockstep).
WEBDAV_BASE_PATH="${WEBDAV_BASE_PATH:-/webdav/}"
# The site's desktop page route (nginx redirects, the `make url` renderer,
# the E2E specs). One value; nginx and the renderer consume the lib through
# the entrypoint/print-url (the frontend literal is pinned by the unit test).
export ALPINE_PAGE="${ALPINE_PAGE:-alpine.html}"
# The guest ext2 filename and its serving directory (build.sh produces it,
# nginx aliases it, the frontend page config references it, the server
# Dockerfile COPYs it). nginx renders the location/alias from the lib value
# via the entrypoint's envsubst; the frontend literal is pinned by the unit
# test (a frontend build cannot source the lib).
#
# ALPINE_PAGE and WEBVM_IMAGE_DIR are EXPORTED: the server entrypoint
# renders nginx.conf through `envsubst` (a CHILD process), which reads the
# ENVIRONMENT — an unexported shell variable renders EMPTY. compose/.env
# only ever provides CONTROL_HOST/SITE_PORT/… (already in the container
# env), so these two lib-defaulted values must be exported here or the
# rendered config carries `location //` / `return 302 ;` (the ext2 and the
# site redirect silently break). Exported from the lib so every consumer
# (entrypoint, print-url.sh, acceptance.sh, Makefile) inherits the fix.
export WEBVM_IMAGE_DIR="${WEBVM_IMAGE_DIR:-custom-disk-images}"
WEBVM_IMAGE_NAME="${WEBVM_IMAGE_NAME:-webvm-custom-disk.ext2}"
# The IndexedDB overlay key prefix (webvm/src/lib/cacheId.js is the frontend
# home; build.sh prints the final cacheId from this value so the shell and
# the page cannot disagree; the frontend literal is pinned by the unit test).
CACHE_ID_PREFIX="${CACHE_ID_PREFIX:-blocks_alpine_}"
# The guest->LAN SSH relay port (gateway socat + acceptance script).
GIT_SSH_PORT="${GIT_SSH_PORT:-2222}"
GIT_HTTP_PORT="${GIT_HTTP_PORT:-8083}"
# The DEFAULT gateway tailnet IP baked into the guest syncrc / the served
# syncUrl when GATEWAY_TAILNET_IP is not yet recorded (bootstrap, CI, and
# direct `docker build` runs). headscale allocates sequentially from
# 100.64.0.1, so this is the expected FIRST gateway IP; the webdav
# fail-closed checks make the recorded value mandatory before the baked
# config is rendered, so this is only a placeholder for the guest-image
# fallbacks (build.sh + the Dockerfile ARG defaults — pinned by the unit
# test so they cannot drift).
GATEWAY_TAILNET_IP_DEFAULT="${GATEWAY_TAILNET_IP_DEFAULT:-100.64.0.1}"
# The periodic storage-reset countdown (OPTIONAL, opt-in): RESET_INTERVAL_HOURS
# empty (default) = facility OFF. Setting it (e.g. 6) enables the sidebar
# countdown AND is what scripts/reset-cycle.sh schedules with (the host-driven
# cycle: stop the stack, wipe the webdav storage, pull the latest commit,
# rebuild, restore). The NEXT deadline (epoch seconds) is written by
# reset-cycle.sh into ${RESET_STATE_DIR}/deadline on the host; that directory
# is bind-mounted into the server container (compose) at the container-side
# path RESET_DEADLINE_FILE, where the entrypoint reads it and bakes it into
# /webvm-config.js so the page can count down. Never a secret — it is served
# to every visitor of a public instance on purpose.
RESET_INTERVAL_HOURS="${RESET_INTERVAL_HOURS:-}"
RESET_STATE_DIR="${RESET_STATE_DIR:-./state/reset}"
RESET_DEADLINE_FILE="${RESET_DEADLINE_FILE:-/etc/webvm/reset/deadline}"
# Secrets stay empty here — the per-mode fail-closed checks (webvm_require_secret)
# enforce them where they are used.
GATEWAY_TAILNET_IP="${GATEWAY_TAILNET_IP:-}"
WEBDAV_USER="${WEBDAV_USER:-}"
WEBDAV_PASS="${WEBDAV_PASS:-}"
GATEWAY_AUTHKEY="${GATEWAY_AUTHKEY:-}"
HEADSCALE_PREAUTHKEY="${HEADSCALE_PREAUTHKEY:-}"
SAMBA_LAN_IP="${SAMBA_LAN_IP:-}"
GIT_SSH_LAN_IP="${GIT_SSH_LAN_IP:-}"
GIT_HTTP_LAN_IP="${GIT_HTTP_LAN_IP:-}"

# --------------------------------------------------------------------------
# .env loader (precedence: explicit environment > .env > defaults above).
# --------------------------------------------------------------------------
# Export .env values that are NOT already in the environment (an explicit env
# var wins). Keys are validated so a stray line cannot become an arbitrary
# shell assignment; surrounding quotes are stripped (compose strips them too,
# so the shell and compose must agree).
webvm_load_dotenv() {
	[ -f .env ] || return 0
	while IFS='=' read -r key rest || [ -n "$key" ]; do
		key=$(printf '%s' "$key" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
		# Key validation, single home: skip comments/empty lines and anything
		# that could become an arbitrary shell assignment. NOTE the negated
		# class form — busybox ash's case/glob mis-matches the positive
		# range form `[A-Za-z0-9_]*` (it lets '-' and ' ' through; verified
		# 2026-08-29), while `*[!A-Za-z0-9_]*` flags them correctly.
		case "$key" in
			*[!A-Za-z0-9_]*) continue ;;
		esac
		# First char must be a letter or underscore (shell variable rules).
		case "$key" in
			[A-Za-z_]*) ;;
			*) continue ;;
		esac
		if ! env | grep -q "^${key}="; then
			rest=$(printf '%s' "$rest" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
				-e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
			export "$key=$rest"
		fi
	done <.env
}

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

# webvm_wait_until <tries> <sleep_s> <cmd...> — run <cmd...> until it succeeds
# or <tries> attempts elapse. Returns 0 on success.
webvm_wait_until() {
	_tries=$1
	_sleep=$2
	shift 2
	_i=0
	while [ "$_i" -lt "$_tries" ]; do
		if "$@"; then
			return 0
		fi
		sleep "$_sleep"
		_i=$((_i + 1))
	done
	return 1
}

# webvm_require_secret <name> <hint> — fail closed when the named env var is
# unset/empty, with a hint pointing at the fix.
webvm_require_secret() {
	_name=$1
	_hint=$2
	eval "_value=\${$_name:-}"
	if [ -z "$_value" ]; then
		echo "FATAL: $_name is required. $_hint" >&2
		exit 1
	fi
}

# webvm_backend_needs_headscale <backend> — exit 0 when the backend requires
# the control plane (headscale + gateway), exit 1 otherwise. The single home
# of the backend->headscale matrix: the server entrypoint's `need_headscale`
# and webvm_require_mode_secrets' `_need` both come from here, so a mode
# added on one side and forgotten on the other can never silently start a
# tailnet-capable backend without its control plane (or vice versa).
# browser/none need headscale ONLY when HEADSCALE_ENABLED=1; samba/webdav
# always do. Unknown backends answer "no" here — the unknown-backend
# rejection lives in webvm_require_mode_secrets (call it before acting).
webvm_backend_needs_headscale() {
	case "$1" in
		browser|none)
			[ "${HEADSCALE_ENABLED}" = "1" ]
			;;
		samba|webdav)
			return 0
			;;
		*)
			return 1
			;;
	esac
}

# webvm_require_mode_secrets <backend> [--bootstrap] [--gateway-key] — the
# per-mode fail-closed secret matrix, shared by the server entrypoint and
# print-url.sh (a mode's requirements live HERE once; both consumers enforce
# the same set, in the same order, so a check added on one side and forgotten
# on the other can never silently weaken the fail-closed guarantee):
#   * browser/none  require HEADSCALE_PREAUTHKEY only when HEADSCALE_ENABLED=1
#   * samba         require HEADSCALE_PREAUTHKEY
#   * webdav        require HEADSCALE_PREAUTHKEY, WEBDAV_USER, WEBDAV_PASS,
#                   and GATEWAY_TAILNET_IP unless --bootstrap (the gateway has
#                   not joined yet during bootstrap)
# --gateway-key additionally requires GATEWAY_AUTHKEY (the server entrypoint
# needs it — the gateway node key; the `make url` printer does not).
webvm_require_mode_secrets() {
	_backend=$1
	_bootstrap=0
	_gateway_key=0
	for _flag in "$@"; do
		case "$_flag" in
			--bootstrap) _bootstrap=1 ;;
			--gateway-key) _gateway_key=1 ;;
		esac
	done
	case "$_backend" in
		browser|none|samba|webdav) ;;
		*)
			echo "Unknown STORAGE_BACKEND: $_backend" >&2
			exit 1
			;;
	esac
	_need=0
	if webvm_backend_needs_headscale "$_backend"; then
		_need=1
	fi
	if [ "$_need" = "1" ] && [ "$_bootstrap" != "1" ]; then
		webvm_require_secret HEADSCALE_PREAUTHKEY "bootstrap: HEADSCALE_BOOTSTRAP=1 docker compose up -d server, then docker compose exec server headscale preauthkeys create --user <id> --reusable --ephemeral --expiration 100y (the BROWSER key is --ephemeral so closed tabs stop accumulating nodes; the user id comes from 'headscale users list'; the first user is 1), and record the printed value in .env (see .env.example)."
		if [ "$_gateway_key" = "1" ]; then
			webvm_require_secret GATEWAY_AUTHKEY "create it with 'headscale preauthkeys create --user <id> --reusable --expiration 100y' (v0.29.x takes the numeric user id, first user = 1; WITHOUT --ephemeral so the gateway node persists and its tailnet IP stays stable) and record it in .env."
		fi
	fi
	case "$_backend" in
		webdav)
			webvm_require_secret WEBDAV_USER "see .env.example"
			webvm_require_secret WEBDAV_PASS "see .env.example"
			if [ "$_bootstrap" != "1" ]; then
				webvm_require_secret GATEWAY_TAILNET_IP "read it from the RUNNING gateway: docker compose exec gateway tailscale ip -4 (or temporarily set HEADSCALE_BOOTSTRAP=1 and read 'headscale nodes list'), record it in .env (see .env.example), then recreate this container."
			fi
			;;
	esac
}

# webvm_supervise_start <name> <log> <cmd...> — start a supervised service
# inside a WRAPPER subshell (the paste-typer pattern, see
# diskimage/rootfs/usr/local/bin/paste-typer.sh): the wrapper records the
# service's real pid in ${WEBVM_SUPERVISE_DIR:-/tmp}/webvm-<name>.pid,
# waits for it, and writes a status marker at
# ${WEBVM_SUPERVISE_DIR:-/tmp}/webvm-<name>.status when it exits. The
# supervisor (webvm_supervise) polls the MARKER, never `kill -0`: in this
# container the entrypoint shell is PID 1 and never reaps its children, so
# an exited service stays a ZOMBIE and kill -0 keeps succeeding — a
# crashed headscale/nginx/tailscaled would otherwise read as healthy and
# compose's restart policy would never fire. The marker file cannot lie.
# Prints the marker path (stdout is otherwise clean: the wrapper and the
# service are fully redirected, so the caller's $(...) capture cannot
# block on a pipe held open by the backgrounded service).
#
# WEBVM_SUPERVISE_STDIN_FD=<fd> (optional): the service's stdin is
# duplicated from that fd at spawn. Needed when the service reads its
# commands from a caller-held fd (the paste-typer's xsendkeys FIFO): an
# asynchronous subshell's stdin is /dev/null, so without this the FIFO
# would never reach the backend. The fd is inherited by the wrapper
# subshell (only 0/1/2 are replaced for async lists).
webvm_supervise_start() {
	_name=$1
	_log=$2
	shift 2
	_dir="${WEBVM_SUPERVISE_DIR:-/tmp}"
	_marker="$_dir/webvm-$_name.status"
	_pidfile="$_dir/webvm-$_name.pid"
	rm -f "$_marker" "$_pidfile"
	(
		if [ -n "${WEBVM_SUPERVISE_STDIN_FD:-}" ]; then
			exec "$@" 0<&"$WEBVM_SUPERVISE_STDIN_FD" >"$_log" 2>&1 &
		else
			"$@" >"$_log" 2>&1 &
		fi
		_svc=$!
		echo "$_svc" > "$_pidfile"
		wait "$_svc"
		echo dead > "$_marker"
	) >/dev/null 2>&1 &
	printf '%s\n' "$_marker"
}

# webvm_kill_supervised <name> — kill the service started by
# webvm_supervise_start (from its pidfile), best-effort. Used on startup
# failure paths where the container exits anyway (cleanup, not recovery).
webvm_kill_supervised() {
	_pidfile="${WEBVM_SUPERVISE_DIR:-/tmp}/webvm-$1.pid"
	kill "$(cat "$_pidfile" 2>/dev/null)" 2>/dev/null || true
}

# webvm_supervise <marker...> — stop the container when any supervised
# service dies (its status marker appears), so compose's restart policy
# brings a healthy stack back up. Watches MARKERS, not pids: kill -0 on a
# zombie succeeds and a crashed-but-unreaped child would never be seen.
# webvm_supervise_once <marker...> — ONE pass of the supervised-marker check:
# exits 1 if any marker exists (the supervised service died), 0 otherwise.
# Used by supervisors that interleave the check with their own work (the
# gateway relay probes); webvm_supervise is the pure loop form.
webvm_supervise_once() {
	for _marker in "$@"; do
		if [ -n "$_marker" ] && [ -f "$_marker" ]; then
			echo "FATAL: supervised service exited (marker $_marker); stopping container" >&2
			return 1
		fi
	done
	return 0
}

webvm_supervise() {
	while :; do
		if ! webvm_supervise_once "$@"; then
			exit 1
		fi
		sleep 3
	done
}

# webvm_key_is_listed <key_value> <masked_listing> — true when the key's
# prefix appears in a `headscale preauthkeys list` output. The pinned
# headscale (0.29.x) prints keys MASKED as a short prefix + *** when the
# output is not a TTY, so match the configured key against the stripped
# prefix (single home of the masked-key matching; used by the server
# entrypoint's fail-closed key check).
# NOTE (inherent imprecision): the masked listing shows only the key's
# leading chars, so two keys sharing that prefix are indistinguishable and
# a never-created key can pass the check. The check is belt-and-braces —
# the wasm client's registration is the real gate — but keep it: it turns
# a typo'd .env key into a clear FATAL at container start.
webvm_key_is_listed() {
	_key=$1
	_listing=$2
	_prefixes=$(printf '%s\n' "$_listing" \
		| grep -oE 'hskey-auth-[A-Za-z0-9_-]+\**' \
		| sed 's/\*\+$//' \
		| sort -u)
	for _prefix in $_prefixes; do
		case "$_key" in
			"$_prefix"*) return 0 ;;
		esac
	done
	return 1
}
