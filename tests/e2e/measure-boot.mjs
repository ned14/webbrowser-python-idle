// Boot-duration benchmark against a RUNNING deployment (live site or
// `make up[-tailnet]`). Measures the frontend's own "boot" span: the moment
// the "Estimated time remaining" pill appears (bootStarted) until it
// disappears (the guest's file manager writes 'webvm desktop ready').
//
// Runs in ONE browser context so run 2 exercises the warm browser disk/HTTP
// cache; the NA run uses CDP network throttling to model a North-America
// reader's extra RTT.
//
// CAVEAT: waitFor({state:'detached'}) also resolves if a stalled boot trips
// the page's watchdog auto-reload (~200 s silence / 270 s floor), so an
// out-of-family long run should be re-checked against the boot console.
//
//   E2E_BASE_URL=https://webvm.nedprod.com:8081  (default https://127.0.0.1:8081)
//   NA_LATENCY_MS=90                              (default 90 ms extra RTT)
//
// Usage:
//   node measure-boot.mjs
import { chromium } from '@playwright/test';

const BASE = process.env.E2E_BASE_URL || 'https://127.0.0.1:8081';
const NA_LATENCY_MS = Number(process.env.NA_LATENCY_MS || 90);
const MAX_BOOT_MS = 480_000;

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();

async function bootOnce(label) {
	const t0 = Date.now();
	await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
	const pill = page.locator('text=Estimated time remaining');
	await pill.waitFor({ state: 'visible', timeout: 90_000 });
	const pillAt = Date.now();
	await pill.waitFor({ state: 'detached', timeout: MAX_BOOT_MS });
	const readyAt = Date.now();
	console.log(`${label}: load->pill ${((pillAt - t0) / 1000).toFixed(1)}s, boot(pill->ready) ${((readyAt - pillAt) / 1000).toFixed(1)}s, total ${((readyAt - t0) / 1000).toFixed(1)}s`);
	return (readyAt - pillAt) / 1000;
}

await bootOnce('cold   ');
await bootOnce('warm   ');

const naCtx = await browser.newContext({ ignoreHTTPSErrors: true });
const naPage = await naCtx.newPage();
const cdp = await naCtx.newCDPSession(naPage);
await cdp.send('Network.enable');
await cdp.send('Network.emulateNetworkConditions', {
	offline: false,
	latency: NA_LATENCY_MS,
	downloadThroughput: -1,
	uploadThroughput: -1,
});
const t0 = Date.now();
await naPage.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
const pill = naPage.locator('text=Estimated time remaining');
await pill.waitFor({ state: 'visible', timeout: 120_000 });
const pillAt = Date.now();
await pill.waitFor({ state: 'detached', timeout: MAX_BOOT_MS });
console.log(`na-lat : load->pill ${((pillAt - t0) / 1000).toFixed(1)}s, boot(pill->ready) ${((Date.now() - pillAt) / 1000).toFixed(1)}s, total ${((Date.now() - t0) / 1000).toFixed(1)}s`);

await naCtx.close();
await browser.close();