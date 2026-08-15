import { expect, test } from '@playwright/test';
import { basicAuthHeaders } from '../lib/webdav-auth.js';

// webdav mode sync (plan §9.4): the guest sync agent must appear on the
// WebDAV backend within ~2 min (lease + first snapshot), and a reload must
// boot cleanly (pull path exercised).
//
// WORKS as of 2026-08-15: the tailscale wasm client was rebuilt from source
// (v1.102.2, custom MessageChannel tun — scripts/tailscale-wasm-entry) and
// the guest sync agent was reworked around CheerpX process/timer quirks.
// See plans/networking-bug.md §16 for the full diagnosis + the applied fixes.
//
// Runs in the webdav CI phase (E2E_WEBDAV_* env provided) and self-skips
// in the browser phase (no E2E_WEBDAV_* env).
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

const authHeaders = basicAuthHeaders(WEBDAV_USER, WEBDAV_PASS);

const enabled = Boolean(SESSION_URL && WEBDAV_BASE && WEBDAV_USER && WEBDAV_PASS);

test.skip(!enabled, 'sync spec needs E2E_WEBDAV_* env (webdav CI phase)');

test('webdav mode: sync agent appears on the backend and pull runs on reload', async ({
	page,
	request,
}) => {
	// Two full VM boots + the 150s/60s/60s assertion budgets exceed the
	// 300s global timeout — give this spec its own budget like
	// desktop.spec.js does.
	test.setTimeout(420_000);
	await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded' });

	// Within ~2 min the lease file and the first home snapshot must appear.
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders })).ok(),
			{ timeout: 150_000, intervals: [5000] }
		)
		.toBe(true);
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'snapshot.tar.gz', { headers: authHeaders })).ok(),
			{ timeout: 60_000, intervals: [5000] }
		)
		.toBe(true);

	// Reload: the boot pull runs and the page boots again.
	await page.reload({ waitUntil: 'domcontentloaded' });
	await expect(page.locator('#display')).toBeVisible({ timeout: 60_000 });
});
