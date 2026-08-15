import { expect, test } from '@playwright/test';

// webdav mode sync (plan §9.4): the guest sync agent must appear on the
// WebDAV backend within ~2 min (lease + first snapshot), and a reload must
// boot cleanly (pull path exercised).
//
// SKIPPED by default. The browser-side tailnet (the CheerpX wasm tailscale
// client) crashes in the pinned CheerpX runtime the moment the guest uses the
// network — `RuntimeError: function signature mismatch` (a wasm call_indirect
// type mismatch inside the tailscale module when its netstack/ipstack
// processes the first packet). This is an upstream CheerpX bug, independent of
// the stack: reproduced with runtime 1.3.7 and 1.3.8, self-hosted and CDN-
// loaded, headed and headless, and with headscale 0.28/0.29. The control plane
// (gateway join), the WebDAV backend and the sync agent's presence in the
// guest are all covered by the server integration tests and the rootfs smoke
// suite; only the end-to-end browser data path is blocked. Re-enable once the
// runtime tailnet works: `E2E_SYNC=1` (plus the E2E_WEBDAV_* vars).
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
const runSync = enabled && process.env.E2E_SYNC === '1';

test.skip(
	!runSync,
	'browser tailnet crashes in the pinned CheerpX runtime (upstream bug); set E2E_SYNC=1 to run anyway'
);

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
