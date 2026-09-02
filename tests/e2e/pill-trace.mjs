#!/usr/bin/env node
// Samples the "Estimated time remaining" pill text every ~5 s from page
// load until it detaches (desktop ready), and reports the trajectory so the
// countdown calibration can be verified against the real boot time.
import { chromium } from 'playwright';

const SITE_URL = process.env.E2E_SITE_URL || 'https://webvm.nedprod.com/alpine.html';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const t0 = Date.now();
await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

const samples = [];
let bootS = null;
const dl = Date.now() + 240000;
while (Date.now() < dl) {
	await page.waitForTimeout(5000);
	const txt = await page.evaluate(() => {
		const pill = [...document.querySelectorAll('div,span')].find((el) =>
			el.textContent && el.textContent.includes('Estimated time remaining') && el.children.length < 3);
		return pill ? pill.textContent.trim().replace(/\s+/g, ' ') : null;
	}).catch(() => null);
	if (!txt) {
		// pill gone = desktop marker (or fatal); distinguish via canvas light
		const light = await page.evaluate(() => {
			const d = document.getElementById('display');
			if (!d || !d.width) return -1;
			const s = document.createElement('canvas');
			s.width = d.width; s.height = d.height;
			const c = s.getContext('2d'); c.drawImage(d, 0, 0);
			let lt = 0;
			try {
				const data = c.getImageData(0, 0, s.width, s.height).data;
				for (let j = 0; j < data.length; j += 4)
					if (data[j] > 150 && data[j+1] > 150 && data[j+2] > 150) lt++;
				return lt / data.length * 4;
			} catch (e) { return 0; }
		}).catch(() => -1);
		if (light > 0.35 || light === -1) { bootS = (Date.now() - t0) / 1000; break; }
		if (light < 0) break;
		samples.push({ t: (Date.now() - t0) / 1000, pill: 'NO-PILL (still booting?)' });
	} else {
		const m = txt.match(/Estimated time remaining (\d+):(\d+)/);
		samples.push({ t: (Date.now() - t0) / 1000, pill: txt.slice(0, 40), remaining: m ? Number(m[1]) * 60 + Number(m[2]) : null });
	}
}
console.log('boot_s =', bootS != null ? bootS.toFixed(1) + 's' : 'NOT-DETECTED');
for (const s of samples) {
	const rem = s.remaining != null ? s.remaining + 's' : s.pill;
	console.log(`  t=${s.t.toFixed(0)}s  remaining=${rem}`);
}
await browser.close();