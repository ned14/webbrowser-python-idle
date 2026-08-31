#!/bin/sh
# One full reset cycle for a PUBLIC instance, driven by a host cron job:
#
#   1. stop the whole stack (docker compose down, tailnet profile)
#   2. wipe the server-side webdav storage (webdav mode only)
#   3. pull the latest commit from the deployment repo (git pull --ff-only)
#   4. rebuild everything (make build: guest image + frontend + containers)
#   5. record the NEXT deadline (now + RESET_INTERVAL_HOURS) in the state dir
#   6. restore the stack (make up-tailnet)
#
# The deadline written in step 5 is bind-mounted into the server container
# (compose: ${RESET_STATE_DIR} -> /etc/webvm/reset) and baked into
# /webvm-config.js at container start, so the page's sidebar shows a live
# countdown to end users ("storage resets in HH:MM:SS").
#
# OPT-IN: RESET_INTERVAL_HOURS must be set in .env (e.g. RESET_INTERVAL_HOURS=6
# for the default six-hour cadence). With it unset the facility is off and this
# script refuses to run.
#
# Example crontab line (runs at 02:00, 08:00, 14:00, 20:00 UTC — the cycle
# itself is safe to re-run early: it is idempotent and the deadline is simply
# recomputed):
#   0 2,8,14,20 * * * cd /path/to/deployment && ./scripts/reset-cycle.sh >> /var/log/webvm-reset.log 2>&1
#
# Dry-run (writes the deadline + prints the steps, runs no docker/git/make):
#   RESET_CYCLE_DRY_RUN=1 ./scripts/reset-cycle.sh
set -eu

# Shared defaults + helpers (scripts/lib/webvm-common.sh; single home of
# RESET_INTERVAL_HOURS / RESET_STATE_DIR / DATA_DIR / STORAGE_BACKEND).
WEBVM_COMMON="${WEBVM_COMMON:-scripts/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"
webvm_load_dotenv

# --- Opt-in gate + validation -------------------------------------------------
if [ -z "${RESET_INTERVAL_HOURS:-}" ]; then
	echo "FATAL: RESET_INTERVAL_HOURS is unset — the reset cycle is opt-in. Set it in .env (e.g. RESET_INTERVAL_HOURS=6) to enable the periodic storage-reset cycle + sidebar countdown." >&2
	exit 1
fi
case "$RESET_INTERVAL_HOURS" in
	''|*[!0-9]*)
		echo "FATAL: RESET_INTERVAL_HOURS must be a positive integer number of hours, got '$RESET_INTERVAL_HOURS'" >&2
		exit 1
		;;
esac
if [ "$RESET_INTERVAL_HOURS" -lt 1 ]; then
	echo "FATAL: RESET_INTERVAL_HOURS must be >= 1, got '$RESET_INTERVAL_HOURS'" >&2
	exit 1
fi
case "$STORAGE_BACKEND" in
	webdav|samba|browser|none) ;;
	*)
		echo "Unknown STORAGE_BACKEND: $STORAGE_BACKEND (webdav|samba|browser|none)" >&2
		exit 1
		;;
esac

STATE_DIR="${RESET_STATE_DIR:-./state/reset}"
DEADLINE_FILE="$STATE_DIR/deadline"
NEXT_DEADLINE=$(( $(date +%s) + RESET_INTERVAL_HOURS * 3600 ))

mkdir -p "$STATE_DIR"
if [ "${RESET_CYCLE_DRY_RUN:-}" = "1" ]; then
	echo "$NEXT_DEADLINE" > "$DEADLINE_FILE"
	echo "dry-run: deadline=$NEXT_DEADLINE ($(date -d "@$NEXT_DEADLINE" 2>/dev/null || date -r "$NEXT_DEADLINE") +${RESET_INTERVAL_HOURS}h) written to $DEADLINE_FILE"
	echo "dry-run: would stop the stack, wipe webdav storage (webdav mode only), git pull --ff-only, make build, then restore with make up-tailnet"
	exit 0
fi

# --- 1. Stop the stack --------------------------------------------------------
echo "==> reset-cycle: stopping the stack"
docker compose --profile tailnet down

# --- 2. Wipe the server-side storage (webdav mode only) ----------------------
# browser/none/samba have no server-side storage to wipe (samba storage lives
# in the guest); the deadline file lives OUTSIDE the webdav share (STATE_DIR),
# so this never touches it.
if [ "$STORAGE_BACKEND" = "webdav" ]; then
	echo "==> reset-cycle: wiping webdav storage at $DATA_DIR"
	rm -rf "$DATA_DIR"/* "$DATA_DIR"/.[!.]* "$DATA_DIR"/..?* 2>/dev/null || true
fi

# --- 3. Pull the latest commit ------------------------------------------------
echo "==> reset-cycle: pulling the latest commit (git pull --ff-only)"
git pull --ff-only

# --- 4. Rebuild everything ----------------------------------------------------
# The frontend rebuild bakes the NEW commit SHA + date into the GitHub tab and
# a new image fingerprint into the cacheId, so browsers boot a fresh overlay.
echo "==> reset-cycle: rebuilding (make build)"
make build

# --- 5. Record the next deadline ---------------------------------------------
# Written AFTER a successful build (a failed pull/build aborts the cycle and
# must NOT move the countdown). The entrypoint reads it at the next container
# start and bakes it into /webvm-config.js.
echo "$NEXT_DEADLINE" > "$DEADLINE_FILE"
echo "==> reset-cycle: next storage reset at epoch $NEXT_DEADLINE (in ${RESET_INTERVAL_HOURS}h)"

# --- 6. Restore the stack -----------------------------------------------------
echo "==> reset-cycle: restoring the stack (make up-tailnet)"
make up-tailnet

echo "==> reset-cycle: cycle complete"
