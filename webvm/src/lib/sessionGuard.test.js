import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// sessionGuard.js is the browser-level single-session guard for the shared
// IndexedDB overlay. It only touches globals at call time, so it is tested
// with fake localStorage / BroadcastChannel / window + fake timers — no DOM.
// Module state (heartbeatTimer/channel) is per-import, so each test loads a
// fresh module instance via dynamic import.

const ORIGIN = 'https://test.example';
const KEY = 'webvm-session.' + ORIGIN;

function fakeStorage() {
	const map = new Map();
	return {
		getItem: (k) => (map.has(k) ? map.get(k) : null),
		setItem: (k, v) => map.set(k, String(v)),
		removeItem: (k) => map.delete(k),
		_items: map,
	};
}

class FakeBroadcastChannel {
	static registry = new Map();
	static reset() {
		FakeBroadcastChannel.registry.clear();
	}
	constructor(name) {
		this.name = name;
		this.onmessage = null;
		if (!FakeBroadcastChannel.registry.has(name)) {
			FakeBroadcastChannel.registry.set(name, new Set());
		}
		FakeBroadcastChannel.registry.get(name).add(this);
	}
	postMessage(data) {
		for (const other of FakeBroadcastChannel.registry.get(this.name) || []) {
			if (other !== this && other.onmessage) other.onmessage({ data });
		}
	}
	close() {
		FakeBroadcastChannel.registry.get(this.name)?.delete(this);
	}
}

async function freshGuard() {
	vi.resetModules();
	return await import('./sessionGuard.js');
}

beforeEach(() => {
	vi.useFakeTimers();
	FakeBroadcastChannel.reset();
	globalThis.localStorage = fakeStorage();
	globalThis.BroadcastChannel = FakeBroadcastChannel;
	globalThis.location = { origin: ORIGIN };
	const reload = vi.fn();
	globalThis.window = {
		location: { reload },
		addEventListener: () => {},
		close: () => {},
	};
});

afterEach(() => {
	vi.useRealTimers();
	delete globalThis.localStorage;
	delete globalThis.BroadcastChannel;
	delete globalThis.location;
	delete globalThis.window;
});

// Advance fake timers and flush the microtasks between timer callbacks.
async function settle(ms) {
	await vi.advanceTimersByTimeAsync(ms);
}

// acquireSessionLock awaits a fake-timer setTimeout for its settle window:
// start it, advance the timers, then await the result.
async function acquire(guard, settleMs = 1000) {
	const p = guard.acquireSessionLock();
	await settle(settleMs);
	return p;
}

describe('acquireSessionLock', () => {
	it('acquires an uncontended lock and writes holder state', async () => {
		const guard = await freshGuard();
		const result = await acquire(guard, 200);
		expect(result).toBe(true);
		const state = JSON.parse(localStorage.getItem(KEY));
		expect(state.token).toBeTruthy();
	});

	it('a live holder is detected via BroadcastChannel ping (no takeover)', async () => {
		const guardA = await freshGuard();
		await acquire(guardA, 200);

		const guardB = await freshGuard();
		expect(await acquire(guardB, 1000)).toBe(false);
	});

	it('reclaims an EXPIRED lock when the holder never answers the ping', async () => {
		localStorage.setItem(KEY, JSON.stringify({
			token: 'dead-tab-token',
			lastSeen: Date.now() - 200000, // far beyond EXPIRY_MS (90s)
		}));
		const guard = await freshGuard();
		// Ping timeout (800ms) + settle window
		expect(await acquire(guard, 1000)).toBe(true);
		const state = JSON.parse(localStorage.getItem(KEY));
		expect(state.token).not.toBe('dead-tab-token');
	});

	it('does NOT reclaim a fresh lock whose holder is unreachable (conservative)', async () => {
		localStorage.setItem(KEY, JSON.stringify({
			token: 'recent-token',
			lastSeen: Date.now() - 1000, // well inside EXPIRY_MS
		}));
		const guard = await freshGuard();
		expect(await acquire(guard, 1000)).toBe(false);
	});
});

describe('heartbeat + ousting', () => {
	it('an ousted tab (token mismatch) stops heartbeating and reloads', async () => {
		const guard = await freshGuard();
		await acquire(guard, 200);
		expect(globalThis.window.location.reload).not.toHaveBeenCalled();

		// Another tab took the lock over: the record is no longer ours.
		localStorage.setItem(KEY, JSON.stringify({
			token: 'other-tab-token',
			lastSeen: Date.now(),
		}));
		await settle(11000); // past the ~10s heartbeat cadence
		expect(globalThis.window.location.reload).toHaveBeenCalledTimes(1);
	});

	it('a holder still owning the lock keeps heartbeating without reloading', async () => {
		const guard = await freshGuard();
		await acquire(guard, 200);
		await settle(11000); // a few heartbeat beats
		expect(globalThis.window.location.reload).not.toHaveBeenCalled();
		const state = JSON.parse(localStorage.getItem(KEY));
		expect(state.lastSeen).toBeGreaterThan(Date.now() - 11000);
	});
});
