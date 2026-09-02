#!/usr/bin/env node
// Overlay-repair test: deletes a set of overlay records between warm boots
// and measures the stall rate. Stage 1: zeroed records only. Stage 2:
// all mismatched records. Stage 3: everything.
// Usage: overlay-repair-test.mjs <stage> <runs>
//   stage 1|2|3: which records to delete between runs.
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const STAGE = process.argv[2] || '1';
const ITER = Number(process.argv[3] || 5);
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const IMG = '/Users/ned/boostish/webvm-custom/webvm/custom-disk-images/webvm-custom-disk.ext2';
const image = readFileSync(IMG);
const imageSize = image.length;
const BLOCK = 131072;
const imageBlocks = Math.ceil(imageSize / BLOCK);
const DB_NAME = process.env.WEBVM_DB || null; // if null, use first cjFS DB

function djb2(buf) {
	let h1 = 0, h2 = 0;
	for (let i = 0; i < buf.length; i++) { h1 = (h1 * 33 + buf[i]) >>> 0; h2 = (h2 * 31 + buf[i]) >>> 0; }
	return [h1, h2];
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

async function findBadRecords() {
	await page.goto(SITE_URL.replace('alpine.html', 'webvm-idb-404'), { waitUntil: 'domcontentloaded' }).catch(() => {});
	await page.waitForTimeout(500);
	const bad = await page.evaluate(async (imgSize) => {
		const dbs = await indexedDB.databases();
		const target = dbs.find((d) => d.name.includes('blocks_alpine'));
		if (!target) return { error: 'no blocks DB', dbs: dbs.map((d) => d.name) };
		const db = await new Promise((res) => { const r = indexedDB.open(target.name); r.onsuccess = () => res(r.result); });
		const store = db.transaction('files').objectStore('files');
		const keysReq = store.getAllKeys();
		const keys = await new Promise((res) => { keysReq.onsuccess = () => res(keysReq.result); });
		const zeroed = [], mismatched = [];
		for (const k of keys) {
			const num = /^\/(\d+)$/.exec(String(k));
			if (!num) continue;
			const n = Number(num[1]);
			if (n >= imgSize / 131072) continue;
			const r2 = store.get(k);
			const v = await new Promise((res) => { r2.onsuccess = () => res(r2.result); });
			if (!v) continue;
			const c = v.contents;
			const total = c.length;
			let h1 = 0, h2 = 0, allZero = true;
			const step = Math.max(1, Math.floor(total / 131072));
			for (let i = 0; i < total; i += step) {
				const b = typeof c[i] === 'number' ? c[i] : (c[i] && c[i][0]);
				if (b) allZero = false;
				h1 = (h1 * 33 + b) >>> 0;
				h2 = (h2 * 31 + b) >>> 0;
			}
			if (allZero) zeroed.push(n);
			else { mismatched.push(n); }
		}
		db.close();
		return { zeroed, mismatched };
	}, imageSize);
	return bad;
}

async function deleteRecords(names) {
	await page.goto(SITE_URL.replace('alpine.html', 'webvm-idb-404'), { waitUntil: 'domcontentloaded' }).catch(() => {});
	await page.waitForTimeout(400);
	const res = await page.evaluate(async (delKeys) => {
		const dbs = await indexedDB.databases();
		const target = dbs.find((d) => d.name.includes('blocks_alpine'));
		if (!target) return 'no-db';
		const db = await new Promise((res) => { const r = indexedDB.open(target.name); r.onsuccess = () => res(r.result); });
		const tx = db.transaction('files', 'readwrite');
		let n = 0;
		for (const k of delKeys) { tx.objectStore('files').delete('/' + k); n++; }
		await new Promise((res) => { tx.oncomplete = () => res(); tx.onerror = () => res(); });
		db.close();
		return 'deleted ' + n;
	}, names);
	return res;
}

async function main() {
	// Prime once (establishes a full overlay).
	const prime = await bootOnce(150000);
	console.log(`prime: ${prime.ok ? 'OK' : 'STALL'}`);
	if (!prime.ok) { console.log('prime failed; overlay state unclear'); }

	const bad = await findBadRecords();
	console.log('bad records: zeroed=[' + bad.zeroed.slice(0, 20).join(',') + '] mismatched=' + bad.mismatched.length);

	let del = [];
	if (STAGE === '1') del = bad.zeroed;
	else if (STAGE === '2') del = bad.zeroed.concat(bad.mismatched);
	else if (STAGE === '3') del = 'ALL';
	if (del === 'ALL') {
		const res = await deleteRecords('ALL');
		console.log('stage 3: wiped whole DB:', res);
		// Full DB wipe is easier via deleteDatabase; redo properly:
		const w = await page.evaluate(async () => {
			const dbs = await indexedDB.databases();
			for (const d of dbs) if (d.name.includes('blocks_alpine')) {
				await new Promise((res) => { const r = indexedDB.deleteDatabase(d.name); r.onsuccess = r.onerror = r.onblocked = () => res(); });
			}
			return 'wiped';
		});
		console.log('full DB wipe:', w);
	} else {
		const res = await deleteRecords(del);
		console.log(`stage ${STAGE}: deleted ${del.length} records: ${res}`);
	}

	let okc = 0;
	for (let i = 1; i <= ITER; i++) {
		const r = await bootOnce(150000);
		if (r.ok) okc++;
		console.log(`run ${i}: ${r.ok ? 'OK' : 'STALL'} at ${(r.ms / 1000).toFixed(0)}s`);
	}
	console.log(`\n=== stage ${STAGE}: ${okc}/${ITER} OK ===`);
	await browser.close();
}
await main();