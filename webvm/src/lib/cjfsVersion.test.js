import { describe, it, expect, vi, afterEach } from 'vitest';
import {
	shouldResetCjfs,
	resetCjfsIfImageChanged,
	deleteCjfsDatabases,
	cjfsMarkerKey,
} from './cjfsVersion.js';

// The guest-persistent CheerpX folder FS wipe: a rebuilt guest image must
// start a fresh cjFS_* store (same policy as blocks_alpine_<image-build>),
// and a browser carrying records from older runtimes/images must be repaired
// on its first boot under this migration (missing marker -> reset). The wipe
// is scoped to THIS app's databases only (the origin is shared by every
// project site of the account), and the migration marker records SUCCESS
// only — a blocked/errored wipe retries on the next boot.

function fakeLocalStorage(initial) {
	const map = new Map(Object.entries(initial || {}));
	return {
		getItem: vi.fn((k) => (map.has(k) ? map.get(k) : null)),
		setItem: vi.fn((k, v) => map.set(k, String(v))),
	};
}

function fakeIndexedDB(dbNames, events = {}) {
	const deleted = [];
	const dbs = dbNames.map((name) => ({ name }));
	return {
		deleted,
		databases: vi.fn(async () => dbs),
		deleteDatabase: vi.fn((name) => {
			const req = {};
			deleted.push(name);
			const event = events[name] || 'success';
			queueMicrotask(() => {
				if (event === 'error') req.onerror && req.onerror();
				else if (event === 'blocked') req.onblocked && req.onblocked();
				else req.onsuccess && req.onsuccess();
			});
			return req;
		}),
	};
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('cjfsVersion', () => {
	it('marker key shares the overlay prefix naming family', () => {
		expect(cjfsMarkerKey()).toBe('blocks_alpine_cjfs-image-build');
	});

	it('shouldResetCjfs: absent marker means reset (one-time migration)', () => {
		expect(shouldResetCjfs(null, 'ddc8cd798bb0')).toBe(true);
		expect(shouldResetCjfs(undefined, 'ddc8cd798bb0')).toBe(true);
	});

	it('shouldResetCjfs: same build means no reset, different build means reset', () => {
		expect(shouldResetCjfs('ddc8cd798bb0', 'ddc8cd798bb0')).toBe(false);
		expect(shouldResetCjfs('deadbeef', 'ddc8cd798bb0')).toBe(true);
	});

	it('deletes only THIS app\'s databases — never foreign cjFS_* / blocks_* stores', async () => {
		// The origin (the GitHub account's github.io) hosts ALL of the
		// account's project sites: foreign CheerpX stores from other projects
		// must survive. The overlay family IS prefixed by the runtime
		// (persistence.spec.js pins "cjFS_/blocks_alpine_<build>/").
		const idb = fakeIndexedDB([
			'cjFS_/files/',
			'cjFS_/blocks_alpine_ddc8cd798bb0/',
			'cjFS_/other-project-fs/', // foreign
			'blocks_alpine_other-project/', // foreign
		]);
		vi.stubGlobal('indexedDB', idb);
		const ok = await deleteCjfsDatabases();
		expect(ok).toBe(true);
		expect(idb.deleted.sort()).toEqual([
			'cjFS_/blocks_alpine_ddc8cd798bb0/',
			'cjFS_/files/',
		]);
	});

	it('falls back to the known "/files/" mount when databases() is unavailable', async () => {
		const idb = fakeIndexedDB([]);
		delete idb.databases;
		vi.stubGlobal('indexedDB', idb);
		const ok = await deleteCjfsDatabases();
		expect(ok).toBe(true);
		expect(idb.deleted).toEqual(['cjFS_/files/']);
	});

	it('reports false (no marker, retry later) when deletion is blocked by another tab', async () => {
		const idb = fakeIndexedDB(['cjFS_/files/'], { 'cjFS_/files/': 'blocked' });
		vi.stubGlobal('indexedDB', idb);
		const ok = await deleteCjfsDatabases();
		expect(ok).toBe(false);
	});

	it('reports false (no marker, retry later) when deletion errors', async () => {
		const idb = fakeIndexedDB(['cjFS_/files/'], { 'cjFS_/files/': 'error' });
		vi.stubGlobal('indexedDB', idb);
		const ok = await deleteCjfsDatabases();
		expect(ok).toBe(false);
	});

	it('resets and records the marker when the image build changed (or marker missing)', async () => {
		const storage = fakeLocalStorage({ 'blocks_alpine_cjfs-image-build': 'oldbuild' });
		const idb = fakeIndexedDB(['cjFS_/files/']);
		vi.stubGlobal('localStorage', storage);
		vi.stubGlobal('indexedDB', idb);
		const reset = await resetCjfsIfImageChanged('newbuild');
		expect(reset).toBe(true);
		expect(idb.deleted).toEqual(['cjFS_/files/']);
		expect(storage.setItem).toHaveBeenCalledWith('blocks_alpine_cjfs-image-build', 'newbuild');
	});

	it('does nothing when the stored marker already matches the image build', async () => {
		const storage = fakeLocalStorage({ 'blocks_alpine_cjfs-image-build': 'samebuild' });
		const idb = fakeIndexedDB(['cjFS_/files/']);
		vi.stubGlobal('localStorage', storage);
		vi.stubGlobal('indexedDB', idb);
		const reset = await resetCjfsIfImageChanged('samebuild');
		expect(reset).toBe(false);
		expect(idb.deleteDatabase).not.toHaveBeenCalled();
		expect(storage.setItem).not.toHaveBeenCalled();
	});

	it('first boot under the migration wipes stale stores (repairs poisoned browsers)', async () => {
		const storage = fakeLocalStorage({}); // no marker yet
		const idb = fakeIndexedDB(['cjFS_/files/']);
		vi.stubGlobal('localStorage', storage);
		vi.stubGlobal('indexedDB', idb);
		const reset = await resetCjfsIfImageChanged('ddc8cd798bb0');
		expect(reset).toBe(true);
		expect(idb.deleted).toEqual(['cjFS_/files/']);
	});

	it('a blocked wipe does NOT record the marker — the next boot retries', async () => {
		const storage = fakeLocalStorage({}); // poisoned, no marker yet
		const idb = fakeIndexedDB(['cjFS_/files/'], { 'cjFS_/files/': 'blocked' });
		vi.stubGlobal('localStorage', storage);
		vi.stubGlobal('indexedDB', idb);
		const reset = await resetCjfsIfImageChanged('ddc8cd798bb0');
		expect(reset).toBe(false);
		expect(storage.setItem).not.toHaveBeenCalled();
		// ...and on the NEXT boot (blocker gone) it retries and succeeds.
		idb.deleteDatabase.mockClear();
		const idb2 = fakeIndexedDB(['cjFS_/files/']);
		vi.stubGlobal('indexedDB', idb2);
		const retried = await resetCjfsIfImageChanged('ddc8cd798bb0');
		expect(retried).toBe(true);
		expect(idb2.deleted).toEqual(['cjFS_/files/']);
		expect(storage.setItem).toHaveBeenCalledWith('blocks_alpine_cjfs-image-build', 'ddc8cd798bb0');
	});

	it('degrades gracefully when storage is blocked', async () => {
		const idb = fakeIndexedDB(['cjFS_/files/']);
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new Error('blocked');
			},
		});
		vi.stubGlobal('indexedDB', idb);
		const reset = await resetCjfsIfImageChanged('build');
		expect(reset).toBe(false);
		expect(idb.deleteDatabase).not.toHaveBeenCalled();
	});
});