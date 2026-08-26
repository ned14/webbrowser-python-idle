import { expect, test } from '../lib/browser.js';
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
	// Two full VM boots + the assertion budgets exceed the 300s global
	// timeout — give this spec its own budget like desktop.spec.js does.
	test.setTimeout(600_000);
	// Test isolation: clear every artifact a PREVIOUS session (or the
	// network.spec that runs first) may have left on the backend. webvm.lock
	// and snapshot.tar.gz already existing would make the polls below pass
	// without THIS boot's agent ever syncing — a false positive.
	await request.delete(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders }).catch(() => {});
	await request.delete(WEBDAV_BASE + 'snapshot.tar.gz', { headers: authHeaders }).catch(() => {});
	await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded' });

	// The lease file must appear. The guest's boot pull (wait_for_tailnet)
	// cycles up to 12 ping attempts at ~15-20s each under the slow CheerpX
	// guest clock (verified 2026-08-16), and the first attempts run before
	// the browser-side tailnet driver has finished, so the worst case is
	// ~4 min — budget generously.
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders })).ok(),
			{ timeout: 240_000, intervals: [5000] }
		)
		.toBe(true);
	// First-sync snapshot: the in-guest tar+gzip of the home runs in the wasm
	// python on a COLD browser (no IDB cache), which has taken up to ~2 min
	// after the lease — same generous budget as the lock.
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'snapshot.tar.gz', { headers: authHeaders })).ok(),
			{ timeout: 240_000, intervals: [5000] }
		)
		.toBe(true);

	// Reload: the boot pull runs and the page boots again.
	await page.reload({ waitUntil: 'domcontentloaded' });
	await expect(page.locator('#display')).toBeVisible({ timeout: 60_000 });
});
