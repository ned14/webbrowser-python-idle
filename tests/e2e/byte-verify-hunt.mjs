#!/usr/bin/env node
// Byte-verify hunt: intercepts every ext2 range request, records the exact
// Range header vs Content-Range vs body length, and hashes each response
// body against the expected slice of the local ext2 file.
// Mode "proxy": roll the requests through nginx, verifying bodies.
// Mode "local": fulfill every request from the local file directly —
//             deterministic-correct data, no nginx/HTTP involved.
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';

const MODE = process.argv[2] || 'proxy';
const ITER = Number(process.argv[3] || 6);
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const IMG = '/Users/ned/boostish/webvm-custom/webvm/custom-disk-images/webvm-custom-disk.ext2';
const image = readFileSync(IMG);
const imageSize = image.length;
const { createHash } = await import('node:crypto');

function sliceSha(start, end) {
	const clampedEnd = Math.min(end + 1, imageSize);
	return createHash('sha256').update(image.subarray(start, clampedEnd)).digest('hex').slice(0, 16);
}

const browser = await chromium.launch({ headless: true });
let mismatches = 0, verified = 0, failures = 0, stalls = 0;

for (let i = 1; i <= ITER; i++) {
	const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
	const page = await ctx.newPage();
	const log = [];
	const t0 = Date.now();
	let bootOk = false, traps = 0;

	page.on('console', (m) => {
		const text = m.text();
		if (text.includes('Unexpected exit') || text.includes('out of bounds')) traps++;
	});
	await page.route('**/custom-disk-images/*.ext2*', async (route) => {
		const req = route.request();
		const range = req.headers()['range'] || null;
		if (range) {
			const m = /bytes=(\d+)-(\d+)/.exec(range);
			if (m) {
				const start = +m[1], end = +m[2];
				if (start < imageSize) {
					if (MODE === 'local') {
						const clampedEnd = Math.min(end, imageSize - 1);
						await route.fulfill({
							status: 206,
							headers: {
								'Content-Range': `bytes ${start}-${clampedEnd}/${imageSize}`,
								'Content-Length': String(clampedEnd - start + 1),
								'Accept-Ranges': 'bytes',
							},
							body: image.subarray(start, clampedEnd + 1),
						});
						verified++;
					} else {
						const resp = await route.fetch();
						const body = await resp.body();
						const cr = resp.headers()['content-range'] || '';
						const cm = /bytes (\d+)-(\d+)\/(\d+)/.exec(cr);
						const bodyOk = cm && +cm[1] === start && +cm[3] === imageSize;
						const hashOk = bodyOk && sliceSha(+cm[1], +cm[2]) === createHash('sha256').update(body).digest('hex').slice(0, 16);
						verified++;
						if (!bodyOk || !hashOk) {
							mismatches++;
							log.push(`MISMATCH req=${range} status=${resp.status()} cr=${cr} bodyLen=${body.length} bodyOk=${bodyOk} hashOk=${hashOk}`);
						}
						await route.fulfill({ response: resp });
					}
				} else {
					failures++;
					log.push(`OUT-OF-BOUNDS REQUEST range=${range} (start ${start} >= size ${imageSize})`);
					await route.fulfill({ status: 416, headers: { 'Content-Range': `bytes */${imageSize}` }, body: '' });
				}
			} else {
				await route.continue();
			}
		} else {
			await route.continue();
		}
	});

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	let last = { nb: 0, light: 0 };
	const deadline = Date.now() + 130000;
	while (Date.now() < deadline) {
		await page.waitForTimeout(1000);
		try {
			last = await page.evaluate(() => {
				const d = document.getElementById('display');
				if (!d || !d.width || !d.height) return { nb: 0, light: 0 };
				const s = document.createElement('canvas');
				s.width = d.width; s.height = d.height;
				const c = s.getContext('2d'); c.drawImage(d, 0, 0);
				try {
					const data = c.getImageData(0, 0, s.width, s.height).data;
					let nb = 0, lt = 0;
					for (let j = 0; j < data.length; j += 4) {
						if (data[j] || data[j+1] || data[j+2]) nb++;
						if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
					}
					return { nb: nb / data.length * 4, light: lt / data.length * 4 };
				} catch (e) { return { nb: 0, light: 0 }; }
			});
		} catch (e) { last = { nb: 0, light: 0 }; }
		if (last.light > 0.35) { bootOk = true; break; }
	}
	if (!bootOk) stalls++;
	console.log(`run ${i}: ${bootOk ? 'OK' : 'STALL'} at ${((Date.now() - t0) / 1000).toFixed(0)}s traps=${traps} ${log.length ? '\n  ' + log.slice(0, 4).join('\n  ') : ''}`);
	await ctx.close();
}
await browser.close();
console.log(`\n=== ${MODE}: ${ITER - stalls}/${ITER} ok, verified=${verified} mismatches=${mismatches} oobRequests=${failures} stalls=${stalls}`);