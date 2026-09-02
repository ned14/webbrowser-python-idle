import { expect, test } from '../lib/browser.js';

// Per-session overlay contract (2026-09-02, see
// plans/diagnose-flaky-boots.md): EVERY load — every backend — boots with a
// fresh ephemeral IndexedDB overlay (`blocks_alpine_<random>`, derived in
// $lib/cacheId.js). Cross-session overlay reuse crashes ~50-60 % of boots
// inside the CheerpX core (verified content-independent on runtimes 1.3.8 +
// 1.3.9), so the shared fixed-name overlay and the session guard were
// removed. Each load sweeps leftover stores from previous loads (bounded
// IndexedDB growth); browser mode therefore keeps files only for the
// current session, and samba/webdav restore from the network backend.

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

async function listOverlayDBs(page) {
	return page.evaluate(async () => {
		try {
			const dbs = await indexedDB.databases();
			// The overlay DB name is prefixed by the runtime
			// (e.g. "cjFS_/blocks_alpine_<id>/"), so match the cacheId part.
			return dbs.map((d) => d.name).filter((n) => n && n.includes('blocks_alpine'));
		} catch (e) {
			return [];
		}
	});
}

test('browser mode: every load gets a fresh overlay; leftovers are swept', async ({ page }) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	// Give the VM enough time to boot and open its overlay device.
	await page.waitForTimeout(45_000);
	const before = await listOverlayDBs(page);
	expect(before.length).toBe(1); // exactly this session's store

	// Reload: the previous session's store must be swept and a NEW store
	// created (different name — the id is random per load).
	await page.reload({ waitUntil: 'domcontentloaded' });
	await page.waitForTimeout(25_000);
	const after = await listOverlayDBs(page);
	expect(after.length).toBe(1);
	expect(after[0]).not.toBe(before[0]);
});

test('concurrent tabs: independent ephemeral sessions (no shared overlay)', async ({
	context,
}) => {
	const page1 = await context.newPage();
	await page1.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await page1.waitForTimeout(30_000); // tab 1 boots with its own overlay

	// Exactly one overlay store on the origin so far (tab 1's own).
	const only = await listOverlayDBs(page1);
	expect(only.length).toBe(1);
	const tab1Db = only[0];

	const page2 = await context.newPage();
	await page2.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await page2.waitForTimeout(30_000); // tab 2 boots with its own overlay

	// Tab 2's sweep cannot delete tab 1's LIVE store (deleteDatabase is
	// blocked while the VM holds it open), so the origin now holds BOTH
	// stores — tab 1's original one and tab 2's fresh one (different id).
	const both = await listOverlayDBs(page1);
	expect(both).toContain(tab1Db);
	expect(both.length).toBe(2);
	const tab2Db = both.find((n) => n !== tab1Db);
	expect(tab2Db).toBeTruthy();
	expect(tab2Db).not.toBe(tab1Db);
});
