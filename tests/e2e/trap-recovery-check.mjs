#!/usr/bin/env node
// Trap-recovery semantics check (post-review):
//  A) A synthetic "Unexpected exit" BEFORE the desktop marker must trigger
//     the one-shot auto-reload, and the second attempt must boot.
//  B) A synthetic trap AFTER the desktop is up must show the overlay, NOT
//     reload (the corrected !fileManagerSeen gate — mid-session traps must
//     never throw away a working session).
import { chromium } from 'playwright';
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const t0 = Date.now();
const navs = [];
page.on('framenavigated', (f) => {
	if (f === page.mainFrame() && f.url() !== 'about:blank') navs.push({ t: Date.now() - t0 });
});

async function canvasLight() {
	return page.evaluate(() => {
		const d = document.getElementById('display');
		if (!d || !d.width) return 0;
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
	}).catch(() => 0);
}
async function waitFor(pred, ms, label) {
	const dl = Date.now() + ms;
	while (Date.now() < dl) {
		const v = await pred();
		if (v) return v;
		await page.waitForTimeout(1000);
	}
	throw new Error('timeout waiting for ' + label);
}
async function waitDesktop(ms) {
	const dl = Date.now() + ms;
	while (Date.now() < dl) {
		if (await canvasLight() > 0.35) return true;
		await page.waitForTimeout(1000);
	}
	return false;
}

// A) pre-desktop trap -> reload
await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3500); // boot started (kernel messages), desktop NOT up yet
const navBefore = navs.length;
await page.evaluate(() => { console.log("Unexpected exit", new Error("pre-desktop-synthetic-trap")); });
await waitFor(() => navs.length > navBefore, 20000, 'auto-reload after pre-desktop trap');
console.log(`A) pre-desktop trap -> auto-reload fired at ${(navs[navs.length-1].t/1000).toFixed(1)}s`);
const okA = await waitDesktop(180000);
console.log(`A) second attempt desktop: ${okA ? 'YES' : 'NO'}`);

// B) post-desktop trap -> overlay, no reload
await page.waitForTimeout(3000);
if (!(await canvasLight() > 0.35)) await waitDesktop(60000);
const navBeforeB = navs.length;
await page.evaluate(() => { console.log("Unexpected exit", new Error("post-desktop-synthetic-trap")); });
await page.waitForTimeout(8000);
const overlay = await page.evaluate(() => {
	const el = document.querySelector('[role="alert"]');
	return el ? el.textContent.slice(0, 90) : null;
}).catch(() => null);
console.log(`B) post-desktop trap: reloads=${navs.length - navBeforeB > 0 ? 'YES (BAD)' : 'none'} overlay=${overlay ? 'YES' : 'none'}`);
console.log(`B) ${overlay ? 'overlay text: ' + JSON.stringify(overlay.slice(0, 60)) : 'OVERLAY MISSING (BAD)'}`);
await browser.close();