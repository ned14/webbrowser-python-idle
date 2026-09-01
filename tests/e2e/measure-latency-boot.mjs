// (latency, boot) pairs for calibrating the boot-ETA model: rolls a 1s
// sampler over the Disk-tab "Backend latency" text from pill-appear until the
// pill detaches (file-manager ready).
import { chromium } from '@playwright/test';

const BASE = process.env.E2E_BASE_URL || 'https://127.0.0.1:8081';

async function oneRun(label, throttle) {
	const browser = await chromium.launch({ headless: true });
	const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
	const page = await ctx.newPage();
	if (throttle) {
		const cdp = await ctx.newCDPSession(page);
		await cdp.send('Network.enable');
		await cdp.send('Network.emulateNetworkConditions', {
			offline: false, latency: throttle,
			downloadThroughput: -1, uploadThroughput: -1,
		});
	}
	await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
	const pill = page.locator('text=Estimated time remaining');
	await pill.waitFor({ state: 'visible', timeout: 120_000 });
	const t0 = Date.now();
	await page.locator('[aria-label=Disk]').hover();
	await page.waitForTimeout(1000);
	const samples = [];
	while (true) {
		const lat = await page.evaluate(() => {
			const panel = document.querySelector('div.w-80');
			if (!panel) return null;
			const m = panel.innerText.match(/Backend latency:\s*(\d+)ms/);
			return m ? Number(m[1]) : null;
		}).catch(() => null);
		if (lat != null) samples.push(lat);
		const detached = await Promise.race([
			pill.waitFor({ state: 'hidden' }).then(() => true).catch(() => false),
			page.waitForTimeout(900).then(() => false),
		]);
		if (detached) break;
	}
	const bootS = (Date.now() - t0) / 1000;
	const sum = samples.reduce((a, b) => a + b, 0);
	const mean = samples.length ? Math.round(sum / samples.length) : null;
	const median = (() => {
		if (!samples.length) return null;
		const a = [...samples].sort((x, y) => x - y);
		return a[Math.floor(a.length / 2)];
	})();
	console.log(`${label}: boot=${bootS.toFixed(1)}s n=${samples.length} early=${samples[0] ?? null}ms last=${samples[samples.length - 1] ?? null}ms mean=${mean}ms median=${median}ms`);
	await browser.close();
}

await oneRun('uk-cold', 0);
await oneRun('na+90ms', 90);