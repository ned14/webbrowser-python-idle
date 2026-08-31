import { writable } from 'svelte/store';
import { browser } from '$app/environment';

// The periodic storage-reset countdown (OPTIONAL, opt-in). When the deployment
// runs scripts/reset-cycle.sh on a host cron (RESET_INTERVAL_HOURS set in
// .env), the server entrypoint bakes the NEXT reset's epoch-seconds into
// /webvm-config.js as window.__webvmConfig.resetDeadline; this module reads it
// once at load and ticks a seconds-remaining store every second. Facility off
// (no resetDeadline baked) => the store stays null and the sidebar renders
// nothing.

// Pure config read: the baked config's resetDeadline is a whole epoch-seconds
// number. Anything else (missing, non-integer, <= 0) means "no countdown".
export function parseResetDeadline(value) {
	if (!Number.isInteger(value) || value <= 0)
		return null;
	return value;
}

// Read the deadline from a config object (tests pass a fake; the app passes
// window.__webvmConfig). The countdown is intentionally read directly from the
// baked config — NOT via the hash -> sessionStorage seed path — so it also
// shows for sessions opened from an explicit `make url` hash.
export function readBakedResetDeadline(source) {
	return parseResetDeadline((source && source.resetDeadline) ?? null);
}

// Whole seconds until the deadline (0 once it passes — a deadline in the past
// simply reads as "reset imminent"). `now` is injectable for tests.
export function secondsUntilDeadline(deadlineEpochSeconds, nowEpochSeconds = Date.now() / 1000) {
	return Math.max(0, Math.floor(deadlineEpochSeconds - nowEpochSeconds));
}

// HH:MM:SS with zero-padded fields (clamps negatives).
export function formatCountdown(totalSeconds) {
	const s = Math.max(0, Math.floor(totalSeconds));
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const sec = s % 60;
	return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

// null = facility off; a number = whole seconds remaining (0 = deadline passed).
export const resetCountdown = writable(null);

if (browser) {
	const deadline = readBakedResetDeadline(window.__webvmConfig);
	if (deadline !== null) {
		const tick = () => {
			const remaining = secondsUntilDeadline(deadline);
			resetCountdown.set(remaining);
			// Stop ticking once the deadline passes: reaching 0 coincides
			// with the reset cycle tearing the container down, and a fresh
			// page load re-reads the new deadline from the baked config.
			if (remaining <= 0) clearInterval(id);
		};
		tick();
		const id = setInterval(tick, 1000);
	}
}
