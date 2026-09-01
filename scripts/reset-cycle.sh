#!/bin/sh
# One full update+reset cycle for a PUBLIC instance, driven by a host cron job:
#
#   1. UPDATE CHECK (service stays up): fetch the remote; when the tracking
#      branch has new commits, pull (--ff-only) and rebuild everything
#      (make build: guest image + frontend + containers). A failing
#      pull/build aborts here — the running deployment keeps serving.
#   2. RESTART/RESET — ONLY when there is something to do:
#      * the backend has SERVER-SIDE storage to reset (webdav only): shut the
#        stack down, wipe the webdav storage, restore;
#      * OR a rebuild happened in step 1: shut down and restore so the NEW
#        images are actually rolled out.
#      A browser/none/samba backend with no upstream changes does NOTHING —
#      no stop/start at all (no storage to reset, nothing new to serve).
#      samba storage lives on the guest / an external server, never here.
#      The restore command matches the mode: tailnet backends (samba/webdav)
#      come back with `make up-tailnet`, browser/none with `make up`.
#
# The storage-reset countdown is OPT-IN: with RESET_INTERVAL_HOURS set, a
# reset cycle writes the next deadline (epoch seconds) to
# ${RESET_STATE_DIR}/deadline (bind-mounted into the server container; the
# entrypoint bakes it into /webvm-config.js so the sidebar shows a live
# "storage resets in HH:MM:SS" countdown). Written AFTER every fallible step
# so a failed cycle never moves it. With RESET_INTERVAL_HOURS unset, the
# update/rebuild path still runs and the (webdav) storage reset + countdown
# are disabled.
#
# Example crontab line (6-hour cadence — 02:00/08:00/14:00/20:00 UTC; safe to
# re-run early: with nothing to do the cycle is a no-op):
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

# --- Validation --------------------------------------------------------------
# RESET_INTERVAL_HOURS is OPT-IN for the storage-reset countdown, NOT a gate
# on the cycle as a whole: the update/rebuild leg must work without it (a
# browser-backend deployment has no server-side storage to reset and no
# countdown, but still wants the nightly pull+rebuild). An explicitly SET but
# invalid interval fails closed so a broken cadence never silently wipes
# storage.
case "${RESET_INTERVAL_HOURS:-}" in
	'') ;;
	*[!0-9]*)
		echo "FATAL: RESET_INTERVAL_HOURS must be a positive integer number of hours, got '$RESET_INTERVAL_HOURS'" >&2
		exit 1
		;;
esac
if [ -n "${RESET_INTERVAL_HOURS:-}" ] && [ "$RESET_INTERVAL_HOURS" -lt 1 ]; then
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
NEXT_DEADLINE=""
if [ -n "${RESET_INTERVAL_HOURS:-}" ]; then
	NEXT_DEADLINE=$(( $(date +%s) + RESET_INTERVAL_HOURS * 3600 ))
fi

mkdir -p "$STATE_DIR"
if [ "${RESET_CYCLE_DRY_RUN:-}" = "1" ]; then
	if [ -n "$NEXT_DEADLINE" ]; then
		echo "$NEXT_DEADLINE" > "$DEADLINE_FILE"
		echo "dry-run: deadline=$NEXT_DEADLINE ($(date -d "@$NEXT_DEADLINE" 2>/dev/null || date -r "$NEXT_DEADLINE") +${RESET_INTERVAL_HOURS}h) written to $DEADLINE_FILE"
	fi
	echo "dry-run: would check upstream (git fetch + rev-list HEAD..@{u}); if changed, git pull --ff-only + make build"
	if [ "$STORAGE_BACKEND" = "webdav" ] && [ -n "$NEXT_DEADLINE" ]; then
		RESTORE_TARGET="up-tailnet"
	else
		case "$STORAGE_BACKEND" in
			browser|none) RESTORE_TARGET="up" ;;
			*) RESTORE_TARGET="up-tailnet" ;;
		esac
	fi
	echo "dry-run: would stop the stack, wipe webdav storage (webdav mode only), write the deadline, then restore with make $RESTORE_TARGET"
	exit 0
fi

# --- 1. Upstream update check (pulls + rebuilds ONLY when upstream changed) --
# `git fetch` failure (offline / no remotes) is NON-fatal: the rebuild is
# skipped (nothing new rolled) and the storage-reset leg below still decides
# for itself. `set -e` would otherwise turn a fetch glitch into a skipped
# storage reset.
rebuilt=0
if git fetch --quiet 2>/dev/null; then
	if git rev-parse --abbrev-ref @{u} >/dev/null 2>&1; then
		if [ -n "$(git rev-list HEAD..@{u} 2>/dev/null)" ]; then
			echo "==> upstream has new commits — pulling + rebuilding (service stays up)"
			git pull --ff-only
			make build
			rebuilt=1
		else
			echo "==> upstream unchanged ($(git rev-parse --short HEAD)) — skipping pull/rebuild"
		fi
	else
		echo "WARNING: no upstream tracking branch for HEAD — cannot check for updates; skipping pull/rebuild" >&2
	fi
else
	echo "WARNING: git fetch failed (offline?) — skipping the upstream pull/rebuild" >&2
fi

# --- 2. Restart/rest only when there is something to do ----------------------
# Storage-reset legs:
#   * webdav: the server-side webdav root (DATA_DIR) is real storage to reset;
#     browser/none have none, samba storage lives on the guest/external server.
# A rebuild (rebuilt=1) always warrants a restart — the new images must be
# served. Neither => the service is NOT touched.
restore_target="up-tailnet"
case "$STORAGE_BACKEND" in
	browser|none) restore_target="up" ;;
esac
needs_reset=0
if [ "$STORAGE_BACKEND" = "webdav" ] && [ -n "$NEXT_DEADLINE" ]; then
	needs_reset=1
fi

if [ "$needs_reset" = "1" ] || [ "$rebuilt" = "1" ]; then
	echo "==> reset-cycle: stopping the stack"
	docker compose --profile tailnet down

	# Wipe the server-side storage (webdav mode only). browser/none/samba
	# have no server-side storage to wipe (samba storage lives in the guest);
	# the deadline file lives OUTSIDE the webdav share (STATE_DIR), so this
	# never touches it.
	if [ "$needs_reset" = "1" ]; then
		echo "==> reset-cycle: wiping webdav storage at $DATA_DIR"
		rm -rf "$DATA_DIR"/* "$DATA_DIR"/.[!.]* "$DATA_DIR"/..?* 2>/dev/null || true
	fi

	# Record the next deadline AFTER every fallible step above succeeded (a
	# failed pull/build/down/wipe aborts the cycle and must NOT move the
	# countdown). Only when the countdown facility is enabled.
	if [ -n "$NEXT_DEADLINE" ]; then
		echo "$NEXT_DEADLINE" > "$DEADLINE_FILE"
		echo "==> reset-cycle: next storage reset at epoch $NEXT_DEADLINE (in ${RESET_INTERVAL_HOURS}h)"
	fi

	echo "==> reset-cycle: restoring the stack (make $restore_target)"
	make "$restore_target"
	echo "==> reset-cycle: cycle complete"
else
	echo "==> reset-cycle: nothing to do — no storage to reset ($STORAGE_BACKEND) and no upstream changes; service left untouched"
fi