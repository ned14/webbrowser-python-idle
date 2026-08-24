#!/usr/bin/env node
// Boot benchmark: fresh context per iteration, measures first-pixels and
// explorer-ready (light window) times. Reports stalls.
import { chromium } from 'playwright';

const ITER = Number(process.argv[2] || 4);
const LABEL = process.argv[3] || 'boot';
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const browser = await chromium.launch({ headless: true });
const results = [];

function probe(page) {
	return page.evaluate(() => {
		const d = document.getElementById('display');
		if (!d || !d.width || !d.height) return { nb: 0, light: 0 };
		const s = document.createElement('canvas');
		s.width = d.width; s.height = d.height;
		const c = s.getContext('2d'); c.drawImage(d, 0, 0);
		try {
			const data = c.getImageData(0, 0, s.width, s.height).data;
			let nb = 0, lt = 0;
			const total = s.width * s.height;
			for (let j = 0; j < data.length; j += 4) {
				if (data[j] || data[j + 1] || data[j + 2]) nb++;
				if (data[j] > 150 && data[j + 1] > 150 && data[j + 2] > 150) lt++;
			}
			return { nb: nb / total, light: lt / total };
		} catch (e) { return { nb: 0, light: 0 }; }
	});
}

for (let i = 1; i <= ITER; i++) {
	const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
	const page = await ctx.newPage();
	const errs = [];
	page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });
	page.on('pageerror', (e) => errs.push('pageerror: ' + String(e).slice(0, 160)));
	const t0 = Date.now();
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	let firstPixels = null, explorer = null;
	let last = { nb: 0, light: 0 };
	const deadline = Date.now() + 120000;
	while (Date.now() < deadline) {
		await page.waitForTimeout(1000);
		const p = await probe(page);
		if (!firstPixels && p.nb > 0.01) firstPixels = Date.now() - t0;
		if (p.light > 0.35) { explorer = Date.now() - t0; break; }
		last = p;
	}
	const r = {
		run: i,
		firstPixelsMs: firstPixels,
		explorerMs: explorer,
		ok: explorer != null,
		final: last,
		errs,
	};
	results.push(r);
	console.log(`run ${i}: firstPixels=${firstPixels ? (firstPixels / 1000).toFixed(1) + 's' : 'N/A'} explorer=${explorer ? (explorer / 1000).toFixed(1) + 's' : 'STALL'} ${errs.length ? 'errors=' + errs.length : ''}`);
	await ctx.close();
}
await browser.close();
const ok = results.filter((r) => r.ok);
const med = (a) => { a.sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : null; };
console.log(`\n=== ${LABEL}: ${ok.length}/${ITER} ok ===`);
if (ok.length) {
	console.log(`explorer median: ${(med(ok.map((r) => r.explorerMs)) / 1000).toFixed(1)}s`);
	console.log(`firstPixels median: ${(med(ok.map((r) => r.firstPixelsMs)) / 1000).toFixed(1)}s`);
}
for (const r of results.filter((x) => !x.ok)) {
	if (r.errs.length) console.log(`stall run ${r.run} errors:`, r.errs.slice(0, 3));
}
