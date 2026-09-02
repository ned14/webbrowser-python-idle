#!/usr/bin/env node
// Probe a WebVM-like page: prints console highlights, canvas state and
// DOM facts so the boot detector can be adapted to upstream pages.
import { chromium } from 'playwright';
const URL = process.argv[2] || 'https://webvm.io/alpine.html';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const t0 = Date.now();
page.on('console', (m) => {
	const t = m.text();
	if (m.type() === 'error' || t.includes('WebVM') || t.includes('ready') || t.includes('boot') || t.includes('Unexpected'))
		console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s ${m.type()}] ${t.slice(0, 130)}`);
});
page.on('pageerror', (e) => console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s PAGEERROR] ${String(e).slice(0, 150)}`));
await page.goto(URL, { waitUntil: 'domcontentloaded' });
console.log('title:', await page.title());
for (let i = 0; i < 24; i++) {
	await page.waitForTimeout(5000);
	const s = await page.evaluate(() => {
		const d = document.getElementById('display');
		const canvases = [...document.querySelectorAll('canvas')].map((c) => ({ id: c.id, w: c.width, h: c.height }));
		const pill = [...document.querySelectorAll('div,span')].find((el) => el.textContent && el.textContent.includes('Estimated time') && el.children.length < 3);
		let light = -1;
		if (d && d.width > 100) {
			const c2 = document.createElement('canvas');
			c2.width = d.width; c2.height = d.height;
			const g = c2.getContext('2d'); g.drawImage(d, 0, 0);
			try {
				const data = g.getImageData(0, 0, c2.width, c2.height).data;
				let lt = 0;
				for (let j = 0; j < data.length; j += 4)
					if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
				light = lt / data.length * 4;
			} catch (e) { light = -2; }
		}
		return { canvases, pill: pill ? pill.textContent.trim().replace(/\s+/g, ' ').slice(0, 60) : null, light };
	}).catch((e) => ({ err: String(e) }));
	if (i % 2 === 0 || s.light > 0.2)
		console.log(`[${((Date.now()-t0)/1000).toFixed(0)}s] ${JSON.stringify(s).slice(0, 220)}`);
	if (s.light > 0.35) { console.log(`DESKTOP at ${((Date.now()-t0)/1000).toFixed(1)}s`); break; }
}
await browser.close();