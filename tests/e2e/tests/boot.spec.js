import { expect, test } from '@playwright/test';
import { waitForDesktop } from '../lib/desktop.js';

// E2E boot + no-egress assertions (plan §9.4).
// The browser-mode case is opened with NO authKey/controlUrl (a disconnected
// session — asserting no auto-login attempt occurs); network params are only
// present in the webdav case (E2E_WEBDAV_URL env). In the webdav CI phase the
// server BAKES the tailnet/sync keys into /webvm-config.js, so the browser
// case pins a dummy hash (`#e2e`) there: any explicit hash disables the baked
// seeding in app.html, keeping this spec a disconnected session.

const CONTROL_HOST = process.env.E2E_CONTROL_HOST || '127.0.0.1';
const CONTROL_PORT = process.env.E2E_CONTROL_PORT || '8443';
// The wasm Tailscale client DROPS the controlUrl port when building its
// control-plane URLs (wss://<host>/ts2021, /derp, /derp/probe), so the
// control plane is also addressed on the scheme-default port 443
// (CONTROL_WSS_PORT, relayed by the gateway in tailnet modes). The no-egress
// allowlist must admit BOTH port families.
const CONTROL_WSS_PORT = process.env.E2E_CONTROL_WSS_PORT || '443';
const isControlPlane = (candidate) =>
	candidate.hostname === CONTROL_HOST &&
	(candidate.port === CONTROL_PORT || candidate.port === CONTROL_WSS_PORT || candidate.port === '');
const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

test('boots the desktop over HTTPS with cross-origin isolation intact', async ({ page }) => {
	const consoleErrors = [];
	page.on('console', (msg) => {
		if (msg.type() !== 'error') return;
		const text = msg.text();
		// Allowlist the blocked-logtail CSP violation. A compliant pinned
		// client self-disables logtail via netmap Debug.DisableLogTail, in
		// which case no warning fires — either way the assertion is that no
		// OTHER error occurs.
		if (/log\.tailscale\.com/.test(text)) return;
		if (/Content Security Policy/.test(text) && /connect-src/.test(text)) return;
		consoleErrors.push(text);
	});

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await waitForDesktop(page);

	const isolation = await page.evaluate(() => ({
		sab: typeof SharedArrayBuffer !== 'undefined',
		coep: typeof crossOriginIsolated === 'boolean' && crossOriginIsolated,
	}));
	expect(isolation.sab).toBe(true);
	expect(isolation.coep).toBe(true);

	// After the strip, the URL hash must be gone (no secrets in history).
	expect(page.url()).not.toMatch(/#authKey=/);
	expect(consoleErrors).toEqual([]);
});

test('makes zero external requests (HTTP and WebSockets)', async ({ page }) => {
	const external = [];
	const pageOrigin = new URL(SITE_URL).origin;
	await page.route('**/*', (route) => {
		const url = new URL(route.request().url());
		if (url.origin !== pageOrigin && !isControlPlane(url)) {
			external.push(url.href);
		}
		route.continue();
	});
	page.on('websocket', (ws) => {
		const url = new URL(ws.url());
		// `||` (NOT `&&`): a WebSocket to a different host on a control port —
		// or any other cross-origin socket — must be flagged, not let through.
		if (!isControlPlane(url)) {
			external.push('ws://' + ws.url());
		}
	});

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await waitForDesktop(page);
	// Let the WASM Tailscale client attempt any (blocked) logtail fetch and
	// settle its connections.
	await page.waitForTimeout(20_000);

	expect(external).toEqual([]);
});

test('serves only same-origin assets (no stock webvm external tags)', async ({ page }) => {
	const assets = [];
	await page.route('**/*', (route) => {
		const url = new URL(route.request().url());
		if (url.origin !== new URL(SITE_URL).origin) assets.push(url.href);
		route.continue();
	});
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await page.waitForTimeout(10_000);
	// plausible/fonts/serviceWorker must be absent; only the control plane is
	// allowed cross-origin (in network modes).
	for (const a of assets) {
		expect(a).not.toMatch(/plausible|googleapis|gstatic|serviceWorker/);
	}
});
