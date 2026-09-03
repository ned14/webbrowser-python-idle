#!/usr/bin/env node
// Visual boot comparison: screenshots at fixed times + canvas/console state.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const URL = process.argv[2];
const LABEL = process.argv[3];
const SHOTS = [15, 30, 45, 60, 90, 120];
mkdirSync('/tmp/bootshots/' + LABEL, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const t0 = Date.now();
const events = [];
page.on('console', (m) => {
	const t = m.text();
	if (m.type() === 'error') events.push(`[${((Date.now()-t0)/1000).toFixed(0)}s err] ${t.slice(0, 90)}`);
});
let bootS = null;
for (let i = 0; i <= 24; i++) {
	await page.waitForTimeout(5000);
	const s = await page.evaluate(() => {
		const d = document.getElementById('display');
		if (!d || !d.width) return { sized: false };
		const c2 = document.createElement('canvas');
		c2.width = d.width; c2.height = d.height;
		const g = c2.getContext('2d'); g.drawImage(d, 0, 0);
		try {
			const data = g.getImageData(0, 0, c2.width, c2.height).data;
			let nz = 0, lt = 0;
			for (let j = 0; j < data.length; j += 4) {
				if (data[j] || data[j+1] || data[j+2]) nz++;
				if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
			}
			return { sized: true, w: d.width, h: d.height, nz: nz / data.length * 4, light: lt / data.length * 4 };
		} catch (e) { return { sized: true, w: d.width, h: d.height }; }
	}).catch(() => ({ sized: false }));
	const t = (Date.now() - t0) / 1000;
	if (SHOTS.includes(Math.round(t))) {
		await page.screenshot({ path: `/tmp/bootshots/${LABEL}/t${Math.round(t)}s.png` });
		console.log(`shot t=${Math.round(t)}s ${JSON.stringify(s)}`);
	}
	if (!bootS && s.sized && s.nz > 0.01) bootS = Math.round(t);
	if (t >= 125) break;
}
console.log(`=== ${LABEL}: first-nonblack-canvas ~${bootS ?? 'never'}s; events:`);
for (const e of events.slice(0, 5)) console.log(' ', e);
await browser.close();