import { expect, test } from '@playwright/test';

// webdav mode sync (plan §9.4): the guest sync agent must appear on the
// WebDAV backend within ~2 min (lease + first snapshot), and a reload must
// boot cleanly (pull path exercised).
//
// Requires:
//   E2E_WEBDAV_URL  — the full session URL (hash carries authKey/controlUrl/
//                      syncUrl/syncUser/syncPass)
//   E2E_WEBDAV_BASE — the WebDAV endpoint, e.g. http://127.0.0.1:8082/webdav/
//   E2E_WEBDAV_USER / E2E_WEBDAV_PASS

const SESSION_URL = process.env.E2E_WEBDAV_URL;
const WEBDAV_BASE = process.env.E2E_WEBDAV_BASE;
const WEBDAV_USER = process.env.E2E_WEBDAV_USER || '';
const WEBDAV_PASS = process.env.E2E_WEBDAV_PASS || '';

const auth = { username: WEBDAV_USER, password: WEBDAV_PASS };

const enabled = Boolean(SESSION_URL && WEBDAV_BASE && WEBDAV_USER && WEBDAV_PASS);

test.skip(!enabled, 'set E2E_WEBDAV_URL/E2E_WEBDAV_BASE/E2E_WEBDAV_USER/E2E_WEBDAV_PASS');

test('webdav mode: sync agent appears on the backend and pull runs on reload', async ({
	page,
	request,
}) => {
	await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded' });

	// Within ~2 min the lease file and the first home snapshot must appear.
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'webvm.lock', { auth })).ok(),
			{ timeout: 150_000, intervals: [5000] }
		)
		.toBe(true);
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'snapshot.tar.gz', { auth })).ok(),
			{ timeout: 60_000, intervals: [5000] }
		)
		.toBe(true);

	// Reload: the boot pull runs and the page boots again.
	await page.reload({ waitUntil: 'domcontentloaded' });
	await expect(page.locator('#display')).toBeVisible({ timeout: 60_000 });
});
