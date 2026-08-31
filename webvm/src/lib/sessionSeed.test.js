import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// The hash -> sessionStorage seed logic lives as an inline script in
// app.html (it MUST run before the bundle, so it cannot be a module import).
// This test executes the REAL script text with fakes — the only way to keep
// the credential-key handling honest without duplicating it.

const appHtml = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), '..', 'app.html'),
	'utf8'
);

const scriptMatch = appHtml.match(
	/<script>\s*(\/\/ Move network\/sync secrets[\s\S]*?)\s*<\/script>/
);
if (!scriptMatch) throw new Error('app.html inline seed script not found');

const SEED_SCRIPT = scriptMatch[1];

function fakeStorage() {
	const map = new Map();
	return {
		getItem: (k) => (map.has(k) ? map.get(k) : null),
		setItem: (k, v) => map.set(k, String(v)),
		removeItem: (k) => map.delete(k),
		_items: map,
	};
}

function runSeed({ hash, config, storage }) {
	const replaceState = vi.fn();
	const fakeWindow = {
		location: { hash, pathname: '/alpine.html', search: '' },
		history: { replaceState },
		__webvmConfig: config,
	};
	const fn = new Function('window', 'sessionStorage', 'URLSearchParams', SEED_SCRIPT);
	fn(fakeWindow, storage, URLSearchParams);
	return { fakeWindow, replaceState };
}

describe('app.html session seeding', () => {
	it('moves an explicit hash into sessionStorage and strips it from history', () => {
		const storage = fakeStorage();
		const hash =
			'#authKey=hskey-auth-x&controlUrl=https%3A%2F%2F127.0.0.1%3A8443&syncUrl=http%3A%2F%2F100.64.0.1%3A8082%2Fwebdav%2F&syncUser=webdav&syncPass=pass';
		const { replaceState } = runSeed({ hash, config: {}, storage });

		expect(storage.getItem('authKey')).toBe('hskey-auth-x');
		expect(storage.getItem('controlUrl')).toBe('https://127.0.0.1:8443');
		expect(storage.getItem('syncUrl')).toBe('http://100.64.0.1:8082/webdav/');
		expect(storage.getItem('syncUser')).toBe('webdav');
		expect(storage.getItem('syncPass')).toBe('pass');
		expect(storage.getItem('webvm-explicit-session')).toBe('1');
		expect(replaceState).toHaveBeenCalledWith(null, '', '/alpine.html');
	});

	it('seeds from the baked config when the URL has no hash', () => {
		const storage = fakeStorage();
		const config = {
			authKey: 'hskey-auth-baked',
			controlUrl: 'https://127.0.0.1:8443',
			syncUrl: 'http://100.64.0.1:8082/webdav/',
			syncUser: 'webdav',
			syncPass: 'baked',
		};
		const { replaceState } = runSeed({ hash: '', config, storage });

		expect(storage.getItem('authKey')).toBe('hskey-auth-baked');
		expect(storage.getItem('syncPass')).toBe('baked');
		expect(storage.getItem('webvm-explicit-session')).toBeNull();
		expect(replaceState).not.toHaveBeenCalled();
	});

	it('a new hash is a complete replacement: stale keys are cleared', () => {
		const storage = fakeStorage();
		storage.setItem('syncUrl', 'http://old.example/webdav/');
		storage.setItem('webvm-explicit-session', '1');
		const hash = '#authKey=hskey-auth-new&controlUrl=https%3A%2F%2F127.0.0.1%3A8443';
		runSeed({ hash, config: {}, storage });

		expect(storage.getItem('authKey')).toBe('hskey-auth-new');
		// The hash omitted syncUrl: the stale value must NOT survive a merge
		// (a credential sent to the wrong control plane would leak a key).
		expect(storage.getItem('syncUrl')).toBeNull();
	});

	it('a hash-less reload of an explicit session keeps its params', () => {
		const storage = fakeStorage();
		storage.setItem('authKey', 'hskey-auth-sticky');
		storage.setItem('controlUrl', 'https://127.0.0.1:8443');
		storage.setItem('webvm-explicit-session', '1');
		// Baked config present but the sticky marker must win.
		const config = { authKey: 'hskey-auth-baked', controlUrl: 'https://127.0.0.1:8443' };
		runSeed({ hash: '', config, storage });

		expect(storage.getItem('authKey')).toBe('hskey-auth-sticky');
	});

	it('an explicit hash disables the baked config entirely', () => {
		const storage = fakeStorage();
		const config = { authKey: 'hskey-auth-baked', controlUrl: 'https://127.0.0.1:8443' };
		const hash = '#authKey=hskey-auth-explicit&controlUrl=https%3A%2F%2F127.0.0.1%3A8443';
		runSeed({ hash, config, storage });

		expect(storage.getItem('authKey')).toBe('hskey-auth-explicit');
	});
});
