// Browser-level single-session guard for the shared IndexedDB overlay.
//
// Because the overlay cache is shared per origin (fixed cacheId
// `blocks_alpine_<image-build>`), two live tabs would share one overlay and
// can corrupt it. This guard uses localStorage + BroadcastChannel:
//
//   * the holder keeps a token + heartbeat (~10s) with ~90s expiry in
//     localStorage, released on pagehide/beforeunload;
//   * a contender pings the holder over BroadcastChannel BEFORE taking over,
//     and only reclaims on expiry AND a missed ping (throttling-safe liveness:
//     a hidden tab's timers may be throttled, but a missed ping plus an expired
//     record is a safe takeover);
//   * a simultaneous-loading race is settled by a short settle window.
//
// When the lock is not acquired, the caller must boot the VM with an ephemeral
// (random) cacheId and show a "session already active in another tab" notice.
const HEARTBEAT_MS = 10000;
const EXPIRY_MS = 90000;
const PING_TIMEOUT_MS = 800;
const SETTLE_MS = 200;

let heartbeatTimer = null;
let channel = null;
let holderToken = null;

function storageKey() {
	return "webvm-session." + location.origin;
}

function readState() {
	try {
		return JSON.parse(localStorage.getItem(storageKey()));
	} catch (e) {
		return null;
	}
}

function writeState(state) {
	localStorage.setItem(storageKey(), JSON.stringify(state));
}

function removeState() {
	try {
		localStorage.removeItem(storageKey());
	} catch (e) {}
}

function newToken() {
	return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// Ask the current holder (if any) whether it is alive.
function pingHolder(token, timeout) {
	return new Promise((resolve) => {
		let done = false;
		const finish = (alive) => {
			if (done) return;
			done = true;
			clearTimeout(timer);
			resolve(alive);
		};
		const timer = setTimeout(() => finish(false), timeout);
		channel.onmessage = (ev) => {
			if (ev.data && ev.data.type === "pong" && ev.data.token === token) {
				finish(true);
			}
		};
		channel.postMessage({ type: "ping", token });
	});
}

function startHeartbeat(token) {
	holderToken = token;
	const beat = () => {
	// Only renew the lock if it is still OURS. A reclaimed lock (another
	// tab took over after our expiry) must not be overwritten — an ousted
	// tab's heartbeat would otherwise resurrect a stale holder and let two
	// tabs write the shared overlay. On a mismatch we stop WITHOUT deleting
	// the record: the new holder legitimately owns it, and removing it would
	// reopen the two-holder window.
	const state = readState();
	if (!state || state.token !== token) {
		clearInterval(heartbeatTimer);
		heartbeatTimer = null;
		holderToken = null;
		if (state && state.token === token) removeState();
		try { channel.close(); } catch (e) {}
		return;
	}
	writeState({ token, lastSeen: Date.now() });
};
beat();
heartbeatTimer = setInterval(beat, HEARTBEAT_MS);
// Reply to liveness pings from contender tabs.
channel.onmessage = (ev) => {
	if (ev.data && ev.data.type === "ping") {
		channel.postMessage({ type: "pong", token });
	}
};
const release = () => {
	clearInterval(heartbeatTimer);
	heartbeatTimer = null;
	holderToken = null;
	// Release only a lock we still own (never the current holder's record).
	const current = readState();
	if (current && current.token === token) removeState();
	try { channel.close(); } catch (e) {}
};
	window.addEventListener("pagehide", release);
	window.addEventListener("beforeunload", release);
}

/**
 * Try to acquire the shared-overlay lock.
 * Resolves to `true` when this tab may mount the shared overlay, `false` when
 * another live tab holds it (the caller should boot an ephemeral session).
 */
export async function acquireSessionLock() {
	if (typeof localStorage === "undefined" || typeof BroadcastChannel === "undefined") {
		return true;
	}
	channel = new BroadcastChannel(storageKey());
	const state = readState();
	const now = Date.now();
	if (state && state.token) {
		// Ping the holder BEFORE taking over (BroadcastChannel is the primary
		// liveness signal). Reclaim only on expiry AND a missed ping — a
		// throttled-but-alive tab must not lose its lock, and its worker-based
		// VM would otherwise keep writing the shared overlay.
		const alive = await pingHolder(state.token);
		if (alive) return false;
		if (now - state.lastSeen < EXPIRY_MS) return false; // not expired; conservative
	}
	const token = newToken();
	writeState({ token, lastSeen: Date.now() });
	// Settle a possible acquire race with another simultaneously-loading tab.
	await new Promise((r) => setTimeout(r, SETTLE_MS));
	const settled = readState();
	if (settled && settled.token === token) {
		startHeartbeat(token);
		return true;
	}
	return false;
}
