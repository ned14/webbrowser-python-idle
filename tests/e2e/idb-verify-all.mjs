#!/usr/bin/env node
// Full overlay verification: dump every record of the shared block overlay
// and compare its bytes against the ext2 slice at recordIndex * 131072.
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const IMG = '/Users/ned/boostish/webvm-custom/webvm/custom-disk-images/webvm-custom-disk.ext2';
const image = readFileSync(IMG);
const imageSize = image.length;
const BLOCK = 131072;
const imageBlocks = Math.ceil(imageSize / BLOCK);

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await ctx.newPage();
await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(8000);
await page.goto(SITE_URL.replace('alpine.html', 'webvm-idb-404'), { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(800);

// Collect per-record sha256 + length (computed in-page, returned compact).
const recs = await page.evaluate(async () => {
	const dbs = await indexedDB.databases();
	const db = await new Promise((res) => { const r = indexedDB.open(dbs[0].name); r.onsuccess = () => res(r.result); });
	const store = db.transaction('files').objectStore('files');
	const keysReq = store.getAllKeys();
	const keys = await new Promise((res) => { keysReq.onsuccess = () => res(keysReq.result); });
	const out = [];
	for (const k of keys) {
		const num = /^\/(\d+)$/.exec(k);
		if (!num) continue; // skip the root "" and non-numeric entries
		const r2 = store.get(k);
		const v = await new Promise((res) => { r2.onsuccess = () => res(r2.result); });
		if (!v) continue;
		// cjFS chunked layout: contents is an array of per-byte numbers
		// (verified /0: 131072 numbers, all zero) OR typed arrays. Normalize.
		const c = v.contents;
		const total = c.length;
		let zero = true;
		let joined = null;
		const first = c[0];
		if (typeof first === 'number') {
			joined = new Uint8Array(total);
			for (let i = 0; i < total; i++) {
				const b = c[i];
				if (b !== 0) zero = false;
				joined[i] = b;
			}
		} else {
			// typed-array chunks
			let off = 0;
			for (const chunk of c) { if (chunk && chunk.length) { off += chunk.length; } }
			joined = new Uint8Array(off);
			off = 0;
			for (const chunk of c) { if (chunk && chunk.length) { joined.set(chunk, off); off += chunk.length; } }
			zero = false;
		}
		// simple JS sha256 over bytes (sync via crypto? not available in page reliably)
		let h1 = 0, h2 = 0; // djb2 fallback hash (fast, unique enough for triage)
		for (let i = 0; i < joined.length; i++) { h1 = (h1 * 33 + joined[i]) >>> 0; h2 = (h2 * 31 + joined[i]) >>> 0; }
		out.push({ key: k, n: Number(num[1]), total, zero, h1, h2, lm: v.lastModified });
	}
	db.close();
	return out;
});

// Node-side: compute the same djb2 for the image blocks and compare.
function djb2(buf) {
	let h1 = 0, h2 = 0;
	for (let i = 0; i < buf.length; i++) { h1 = (h1 * 33 + buf[i]) >>> 0; h2 = (h2 * 31 + buf[i]) >>> 0; }
	return [h1, h2];
}
let mismatch = [], zeroed = [], ok = 0;
for (const r of recs) {
	if (r.n >= imageBlocks) { mismatch.push({ ...r, why: 'beyond-image' }); continue; }
	const slice = image.subarray(r.n * BLOCK, Math.min((r.n + 1) * BLOCK, imageSize));
	const [h1, h2] = djb2(slice);
	const matches = h1 === r.h1 && h2 === r.h2;
	const isZero = r.total > 0 && r.zero;
	if (isZero) zeroed.push(r.n);
	else if (!matches) mismatch.push({ ...r, why: `hash ${h1},${h2} vs image ${r.h1},${r.h2}` });
	else ok++;
}
console.log(`records: ${recs.length}  ok=${ok}  allZero=${zeroed.length}  mismatched=${mismatch.length}`);
if (zeroed.length) console.log('all-zero records (block indexes):', zeroed.slice(0, 40).join(','));
for (const m of mismatch.slice(0, 15)) console.log('MISMATCH /' + m.n, m.why.slice(0, 80), 'total=' + m.total);
await browser.close();