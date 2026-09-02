#!/usr/bin/env node
// Warm-boot isolation: ONE context, N sequential loads of alpine.html.
// mode 'idb-only' (default): ext2 range responses are re-served by
// Playwright with Cache-Control:no-store — the browser HTTP cache never
// stores the image, so only the shared IndexedDB overlay is warm.
// mode 'normal': stock behavior (warm IDB + warm HTTP cache).
// Follows page auto-reloads (the watchdog/trap retry) to the final outcome.
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';

const ITER = Number(process.argv[2] || 8);
const MODE = process.argv[3] || 'idb-only';
const MAX_ATTEMPTS = process.argv[4] || 'follow'; // 'follow' | 'first'
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const IMG = '/Users/ned/boostish/webvm-custom/webvm/custom-disk-images/webvm-custom-disk.ext2';
const image = readFileSync(IMG);
const imageSize = image.length;

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();

if (MODE === 'idb-only') {
	await page.route('**/custom-disk-images/*.ext2*', async (route) => {
		const range = route.request().headers()['range'] || null;
		if (range) {
			const m = /bytes=(\d+)-(\d+)/.exec(range);
			if (m && +m[1] < imageSize) {
				const start = +m[1], clampedEnd = Math.min(+m[2], imageSize - 1);
				await route.fulfill({
					status: 206,
					headers: {
						'Content-Range': `bytes ${start}-${clampedEnd}/${imageSize}`,
						'Content-Length': String(clampedEnd - start + 1),
						'Accept-Ranges': 'bytes',
						'Cache-Control': 'no-store',
						'Last-Modified': 'Wed, 02 Sep 2026 00:00:00 GMT',
					},
					body: image.subarray(start, clampedEnd + 1),
				});
			} else if (!m) { await route.continue(); }
			else { await route.fulfill({ status: 416, headers: { 'Content-Range': `bytes */${imageSize}` }, body: '' }); }
		} else {
			// The page's full-image warm fetch: fulfill from the file too.
			await route.fulfill({
				status: 200,
				headers: {
					'Content-Length': String(imageSize),
					'Cache-Control': 'no-store',
					'Last-Modified': 'Wed, 02 Sep 2026 00:00:00 GMT',
				},
				body: image,
			});
		}
	});
}

async function pollBoot(waitMs) {
	const t0 = Date.now();
	let last = { light: 0, w: 0, h: 0 };
	const dl = Date.now() + waitMs;
	while (Date.now() < dl) {
		await page.waitForTimeout(1000);
		last = await page.evaluate(() => {
			const d = document.getElementById('display');
			if (!d || !d.width) return { light: 0, w: 0, h: 0 };
			const s = document.createElement('canvas');
			s.width = d.width; s.height = d.height;
			const c = s.getContext('2d'); c.drawImage(d, 0, 0);
			let lt = 0;
			try {
				const data = c.getImageData(0, 0, s.width, s.height).data;
				for (let j = 0; j < data.length; j += 4)
					if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
				return { light: lt / data.length * 4, w: d.width, h: d.height };
			} catch (e) { return { light: 0, w: d.width, h: d.height }; }
		}).catch(() => ({ light: 0, w: 0, h: 0 }));
		if (last.light > 0.35) return { ok: true, ms: Date.now() - t0 };
		const alert = await page.evaluate(() => {
			const el = document.querySelector('[role="alert"]');
			return el ? el.textContent.slice(0, 80) : null;
		}).catch(() => null);
		if (alert) return { ok: false, ms: Date.now() - t0, alert };
	}
	return { ok: false, ms: Date.now() - t0, last };
}

// Prime (cold) boot so the shared IDB cache is warm.
await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
const prime = await pollBoot(120000);
console.log(`prime: ${prime.ok ? 'OK' : 'STALL'}${prime.alert ? ' [' + prime.alert + ']' : ''}`);

const summary = [];

if (MODE === 'warm-http') {
	// Between runs: unload the page (closes the VM + its IDB handles), wipe
	// ALL IndexedDB databases, so each boot is COLD-IDB + WARM-HTTP-cache.
	async function wipeIdb() {
		// Navigate to a same-origin page that does NOT start the VM (a 404),
		// so the VM's IndexedDB handles are closed but the origin grants IDB
		// access (about:blank is opaque and denies it).
		await page.goto('https://127.0.0.1:8081/webvm-idb-wipe-404', { waitUntil: 'domcontentloaded' }).catch(() => {});
		await page.waitForTimeout(800);
		const names = await page.evaluate(async () => {
			const dbs = await indexedDB.databases();
			for (const db of dbs) await new Promise((res) => {
				const r = indexedDB.deleteDatabase(db.name);
				r.onsuccess = r.onerror = r.onblocked = () => res();
			});
			return dbs.map((d) => d.name);
		});
		return names;
	}
	for (let i = 1; i <= ITER; i++) {
		const t0 = Date.now();
		const wiped = await wipeIdb();
		await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
		const r = await pollBoot(180000);
		summary.push({ i, ok: r.ok, ms: r.ms, attempts: 1, alert: r.alert || '' });
		console.log(`warm-http run ${i}: ${r.ok ? 'OK' : 'STALL'} at ${(r.ms / 1000).toFixed(0)}s wiped=[${wiped.join(',')}]${r.alert ? ' [' + r.alert.slice(0, 70) + ']' : ''}`);
	}
	await browser.close();
	const oks = summary.filter((x) => x.ok).length;
	console.log(`\n=== ${MODE}: ${oks}/${ITER} warm loads reached the desktop ===`);
	process.exit(0);
}

for (let i = 1; i <= ITER; i++) {
	const t0 = Date.now();
	const navs = [];
	page.on('framenavigated', (f) => {
		if (f === page.mainFrame() && f.url() !== 'about:blank' && Date.now() - t0 > 2000)
			navs.push(Date.now() - t0);
	});
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	let runErrs = [];
	const errTap = (m) => { const t = m.text(); if (m.type() === 'error' || t.includes('[WebVM]') || t.includes('Unexpected exit')) runErrs.push(t.slice(0, 160)); };
	page.on('console', errTap);
	let attempts = 1, r = await pollBoot(MAX_ATTEMPTS === 'first' ? 180000 : 300000);
	page.off('console', errTap);
	if (!r.ok && MAX_ATTEMPTS === 'follow' && !r.alert) {
		// A reload may be in flight (watchdog/trap retry); give it one more window.
		await page.waitForTimeout(3000);
		r = await pollBoot(240000);
		if (!r.ok && !r.alert) {
			const still = await pollBoot(90000);
			r = still;
		}
	}
	attempts = navs.length + 1;
	const res = { i, ok: r.ok, ms: r.ms, attempts, alert: r.alert || '', navs, errs: runErrs };
	summary.push(res);
	console.log(`warm${MODE === 'idb-only' ? ' (idb-only)' : ''} run ${i}: ${res.ok ? 'OK' : 'STALL'} at ${(res.ms / 1000).toFixed(0)}s attempts=${attempts}${res.alert ? ' [' + res.alert.slice(0, 70) + ']' : ''}${!r.ok && runErrs.length ? ' errs=[' + runErrs[0].slice(0, 100) + ']' : ''}`);
}
await browser.close();
const oks = summary.filter((x) => x.ok).length;
console.log(`\n=== ${MODE}: ${oks}/${summary.length} warm loads reached the desktop (with retries) ===`);