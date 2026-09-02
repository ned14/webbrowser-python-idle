#!/usr/bin/env node
// Clean cross-boot experiment: prime boot -> full DB wipe -> N boots with
// NO further wipes. Boot 1 reads only the prime's clean records; later
// boots read the previous clean boots' records. Measures whether ANY
// cross-boot reuse crashes, and at what rate.
import { chromium } from 'playwright';

const ITER = Number(process.argv[2] || 6);
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();

async function bootOnce(waitMs, label) {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	const t0 = Date.now();
	const dl = Date.now() + waitMs;
	let attempt = 1;
	while (Date.now() < dl) {
		await page.waitForTimeout(1000);
		const p = await page.evaluate(() => {
			const d = document.getElementById('display');
			if (!d || !d.width) return { light: 0 };
			const s = document.createElement('canvas');
			s.width = d.width; s.height = d.height;
			const c = s.getContext('2d'); c.drawImage(d, 0, 0);
			let lt = 0;
			try {
				const data = c.getImageData(0, 0, s.width, s.height).data;
				for (let j = 0; j < data.length; j += 4)
					if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
				return { light: lt / data.length * 4 };
			} catch (e) { return { light: 0 }; }
		}).catch(() => ({ light: 0 }));
		if (p.light > 0.35) return { ok: true, ms: Date.now() - t0 };
	}
	return { ok: false, ms: Date.now() - t0 };
}

async function wipeDb() {
	await page.goto(SITE_URL.replace('alpine.html', 'webvm-idb-404'), { waitUntil: 'domcontentloaded' }).catch(() => {});
	await page.waitForTimeout(600);
	return page.evaluate(async () => {
		const dbs = await indexedDB.databases();
		for (const d of dbs) if (d.name.includes('blocks_alpine')) {
			await new Promise((res) => { const r = indexedDB.deleteDatabase(d.name); r.onsuccess = r.onerror = r.onblocked = () => res(); });
		}
		return dbs.map((d) => d.name);
	});
}

const prime = await bootOnce(150000, 'prime');
console.log(`prime: ${prime.ok ? 'OK' : 'STALL'}`);
const wiped = await wipeDb();
console.log('wiped:', wiped.join(','));

for (let i = 1; i <= ITER; i++) {
	const r = await bootOnce(150000, `run ${i}`);
	console.log(`run ${i}: ${r.ok ? 'OK' : 'STALL'} at ${(r.ms / 1000).toFixed(0)}s`);
}
await browser.close();