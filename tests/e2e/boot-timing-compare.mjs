#!/usr/bin/env node
// Sequential boot-timing comparison usable for BOTH light and dark themed
// WebVM pages. Metrics: canvas-sized time, first-non-black-pixel time,
// "content" time (sustained non-black > 0.2 % — dark desktops included),
// and 130 s light ratio. Prints one JSON line per run.
import { chromium } from 'playwright';

const URL = process.argv[2];
const LABEL = process.argv[3];
const RUNS = Number(process.argv[4] || 3);

async function oneRun() {
	const browser = await chromium.launch({ headless: true });
	const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
	const page = await ctx.newPage();
	const t0 = Date.now();
	const errs = [];
	page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 100)); });
	await page.goto(URL, { waitUntil: 'domcontentloaded' });
	let sized = null, firstNzb = null, content = null;
	const dl = Date.now() + 130000;
	while (Date.now() < dl) {
		await page.waitForTimeout(2000);
		const s = await page.evaluate(() => {
			const d = document.getElementById('display');
			if (!d) return null;
			const c2 = document.createElement('canvas');
			c2.width = d.width || 1; c2.height = d.height || 1;
			const g = c2.getContext('2d');
			try { g.drawImage(d, 0, 0); } catch (e) { return { w: d.width, h: d.height, nz: -1 }; }
			try {
				const data = g.getImageData(0, 0, c2.width, c2.height).data;
				let nz = 0, lt = 0;
				const step = Math.max(1, Math.floor(data.length / 4 / 262144)); // cap ~256k samples
				for (let j = 0; j < data.length; j += 4 * step) {
					if (data[j] || data[j+1] || data[j+2]) nz++;
					if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
				}
				const tot = data.length / 4 / step;
				return { w: d.width, h: d.height, nz: nz / tot * 100, light: lt / tot * 100 };
			} catch (e) { return { w: d.width, h: d.height, nz: -2 }; }
		}).catch(() => null);
		const t = (Date.now() - t0) / 1000;
		if (!s) continue;
		if (!sized && s.w > 300) sized = t;
		if (!firstNzb && s.nz > 0.01) firstNzb = t;
		if (!content && s.nz > 0.2) content = t;
		if (sized && content) {
			// keep sampling 8 s to confirm stability, then stop
			if (Date.now() - t0 > (content * 1000 + 8000)) break;
		}
	}
	console.log(JSON.stringify({ label: LABEL, sized: sized ? +sized.toFixed(0) : null, firstNzb: firstNzb ? +firstNzb.toFixed(0) : null, content: content ? +content.toFixed(0) : null, errs: errs.slice(0, 2) }));
	await browser.close();
}

for (let i = 0; i < RUNS; i++) await oneRun();