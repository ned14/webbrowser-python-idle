import { expect, test } from '../lib/browser.js';
import { waitForDesktop, lightRatio } from '../lib/desktop.js';
import { basicAuthHeaders } from '../lib/webdav-auth.js';

const GATEWAY_IP = process.env.E2E_GATEWAY_IP || '';
const WEBDAV_BASE = process.env.E2E_WEBDAV_BASE;
const WEBDAV_USER = process.env.E2E_WEBDAV_USER || '';
const WEBDAV_PASS = process.env.E2E_WEBDAV_PASS || '';
const authHeaders = basicAuthHeaders(WEBDAV_USER, WEBDAV_PASS);
const enabled = Boolean(GATEWAY_IP && WEBDAV_BASE && WEBDAV_USER && WEBDAV_PASS);
test.skip(!enabled, 'needs E2E_GATEWAY_IP + E2E_WEBDAV_* env');

test('root visit with OLD chromium: data path reaches the gateway relay', async ({ page, request }) => {
	test.setTimeout(480_000);
	await page.goto('/', { waitUntil: 'domcontentloaded' });
	await waitForDesktop(page);
	await request.delete(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders });
	await expect
		.poll(async () => (await request.get(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders })).ok(), { timeout: 240_000, intervals: [5000] })
		.toBe(true);
	await page.waitForFunction(
		() => {
			const x = window.__webvmNetDiag;
			return x && x.ok;
		},
		{ timeout: 240_000 }
	);
});
