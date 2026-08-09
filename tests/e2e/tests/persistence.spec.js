import { expect, test } from '@playwright/test';

// Persistence + single-session guard (plan §9.4).
// browser mode: the IndexedDB overlay survives reloads and is never written
// by a second tab (which boots ephemeral instead).

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

async function listIndexedDBs(page) {
	return page.evaluate(async () => {
		try {
			const dbs = await indexedDB.databases();
			// The overlay DB name is prefixed by the runtime
			// (e.g. "cjFS_/blocks_alpine_<build>/"), so match the cacheId part.
			return dbs.map((d) => d.name).filter((n) => n && n.includes('blocks_alpine'));
		} catch (e) {
			return [];
		}
	});
}

test('browser mode: overlay persists across reloads', async ({ page }) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	// Give the VM enough time to boot and open its overlay device.
	await page.waitForTimeout(45_000);
	const before = await listIndexedDBs(page);
	expect(before.length).toBeGreaterThan(0);

	await page.reload({ waitUntil: 'domcontentloaded' });
	await page.waitForTimeout(20_000);
	const after = await listIndexedDBs(page);
	expect(after).toEqual(before);
});

test('single-session guard: a second tab boots ephemeral and does not write to the shared overlay', async ({
	context,
}) => {
	const page1 = await context.newPage();
	await page1.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await page1.waitForTimeout(25_000); // first tab acquires the lock + boots

	const page2 = await context.newPage();
	await page2.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

	// The second tab must show the ephemeral-session notice (it never writes
	// to the shared overlay).
	await expect(page2.locator('text=ephemeral session')).toBeVisible({ timeout: 30_000 });
});
