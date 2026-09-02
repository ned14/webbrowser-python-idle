#!/usr/bin/env node
// Discriminator: keep ONLY byte-perfect overlay records (verified against
// the ext2), then measure warm-boot stall rate.
//  - stalls persist  => the IDB READ PATH itself is the bug (any record
//                       read risks corruption), not record content.
//  - stalls vanish   => specific poisoned records cause the crashes.
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const ITER = Number(process.argv[2] || 6);
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const IMG = '/Users/ned/boostish/webvm-custom/webvm/custom-disk-images/webvm-custom-disk.ext2';
const image = readFileSync(IMG);
const BLOCK = 131072;
const imageBlocks = Math.ceil(image.length / BLOCK);

// Precompute image block hashes (djb2x2 — cheap and distinct enough).
const imageHashes = [];
for (let n = 0; n < imageBlocks; n++) {
	const slice = image.subarray(n * BLOCK, Math.min((n + 1) * BLOCK, image.length));
	let h1 = 0, h2 = 0;
	for (let i = 0; i < slice.length; i++) { h1 = (h1 * 33 + slice[i]) >>> 0; h2 = (h2 * 31 + slice[i]) >>> 0; }
	imageHashes.push([h1, h2]);
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();

async function bootOnce(waitMs) {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	const t0 = Date.now();
	const dl = Date.now() + waitMs;
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

// Prime once (builds the overlay).
const prime = await bootOnce(150000);
console.log(`prime: ${prime.ok ? 'OK' : 'STALL'}`);

// Pass image hashes into the page and delete every record that does not
// match its image block.
await page.goto(SITE_URL.replace('alpine.html', 'webvm-idb-404'), { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(500);
const res = await page.evaluate(async (imgHashes) => {
	const dbs = await indexedDB.databases();
	const target = dbs.find((d) => d.name.includes('blocks_alpine'));
	if (!target) return { error: 'no-db' };
	const db = await new Promise((res) => { const r = indexedDB.open(target.name); r.onsuccess = () => res(r.result); });
	const store = db.transaction('files').objectStore('files');
	const keysReq = store.getAllKeys();
	const keys = await new Promise((res) => { keysReq.onsuccess = () => res(keysReq.result); });
	const delKeys = [];
	let kept = 0;
	for (const k of keys) {
		const num = /^\/(\d+)$/.exec(String(k));
		if (!num) { continue; }
		const n = Number(num[1]);
		const ih = imgHashes[n];
		if (!ih) { delKeys.push(k); continue; }
		const r2 = db.transaction('files').objectStore('files').get(k);
		const v = await new Promise((res) => { r2.onsuccess = () => res(r2.result); });
		if (!v) continue;
		const c = v.contents;
		const step = Math.max(1, Math.floor(c.length / 131072));
		let h1 = 0, h2 = 0;
		for (let i = 0; i < c.length; i += step) {
			const b = typeof c[i] === 'number' ? c[i] : (c[i] && c[i][0]);
			h1 = (h1 * 33 + b) >>> 0; h2 = (h2 * 31 + b) >>> 0;
		}
		if (h1 === ih[0] && h2 === ih[1]) kept++;
		else delKeys.push(k);
	}
	if (delKeys.length) {
		const tx = db.transaction('files', 'readwrite');
		for (const k of delKeys) tx.objectStore('files').delete(k);
		await new Promise((res) => { tx.oncomplete = () => res(); tx.onerror = () => res(); });
	}
	db.close();
	return { del: delKeys.length, kept };
}, imageHashes);
console.log('overlay sanitized:', JSON.stringify(res));

let okc = 0;
for (let i = 1; i <= ITER; i++) {
	const r = await bootOnce(150000);
	if (r.ok) okc++;
	console.log(`run ${i}: ${r.ok ? 'OK' : 'STALL'} at ${(r.ms / 1000).toFixed(0)}s`);
}
console.log(`\n=== sanitized-overlay: ${okc}/${ITER} OK ===`);
await browser.close();