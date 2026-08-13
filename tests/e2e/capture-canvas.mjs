// Capture the guest canvas once IDLE/tk reaches mainloop, and report light-pixel
// ratio (the desktop.spec.js §3 assertion) + save a screenshot.
// Usage: node capture-canvas.mjs [outDir]
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const OUT_DIR = process.argv[2] || '.';
mkdirSync(OUT_DIR, { recursive: true });

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
const page = await context.newPage();

// drain the console into a rolling buffer
await page.addInitScript(() => {
	window.__cap = '';
	const iv = setInterval(() => {
		const t = window.__webvmTerm;
		if (t && !t.__c) {
			t.__c = true;
			const ow = t.write.bind(t);
			t.write = (d) => {
				window.__cap += d instanceof Uint8Array ? new TextDecoder().decode(d) : String(d);
				return ow(d);
			};
			clearInterval(iv);
		}
	}, 50);
});

console.log('opening', SITE_URL);
await page.goto(SITE_URL, { waitUntil: 'domcontentloaded', timeout: 120_000 });
const start = Date.now();
let sawMarker = false;
while (Date.now() - start < 8 * 60 * 1000) {
	const c = await page.evaluate(() => window.__cap || '');
	if (c.includes('TRACE_MAINLOOP_BEGIN')) { sawMarker = true; console.log('mainloop marker seen'); break; }
	await new Promise((r) => setTimeout(r, 2000));
}
if (!sawMarker) {
	console.log('mainloop marker NOT seen. console tail:');
	console.log((await page.evaluate(() => (window.__cap || '').slice(-2000))));
	await browser.close();
	process.exit(2);
}

// wait for the canvas to show pixels, then measure + screenshot
let stats = null;
for (let i = 0; i < 60; i++) {
	stats = await page.evaluate(() => {
		const display = document.getElementById('display');
		if (!display || !display.width || !display.height) return null;
		const s = document.createElement('canvas');
		s.width = display.width; s.height = display.height;
		const ctx = s.getContext('2d');
		try {
			ctx.drawImage(display, 0, 0);
			const d = ctx.getImageData(0, 0, s.width, s.height).data;
			let light = 0, any = 0;
			for (let j = 0; j < d.length; j += 4) {
				if (d[j] || d[j+1] || d[j+2]) any++;
				if (d[j] > 150 && d[j+1] > 150 && d[j+2] > 150) light++;
			}
			return { width: s.width, height: s.height, light, total: s.width * s.height, any };
		} catch (e) { return null; }
	});
	if (stats && stats.any > 0) break;
	await new Promise((r) => setTimeout(r, 3000));
}
if (!stats) { console.log('canvas never readable'); await browser.close(); process.exit(2); }

const ratio = stats.light / stats.total;
console.log(`canvas ${stats.width}x${stats.height} light=${stats.light}/${stats.total} ratio=${ratio.toFixed(3)} (desktop test needs > 0.35)`);
await page.screenshot({ path: join(OUT_DIR, 'canvas.png') });
console.log('screenshot saved:', join(OUT_DIR, 'canvas.png'));

// also dump a downscaled ASCII-ish summary of where the light pixels are
const band = await page.evaluate(() => {
	const display = document.getElementById('display');
	const s = document.createElement('canvas');
	s.width = 56; s.height = 18;
	const ctx = s.getContext('2d');
	ctx.drawImage(display, 0, 0, 56, 18);
	return Array.from(ctx.getImageData(0, 0, 56, 18).data);
});
let ascii = '';
for (let r = 0; r < 18; r++) {
	for (let c = 0; c < 56; c++) {
		const i = (r * 56 + c) * 4;
		const lum = (band[i] + band[i+1] + band[i+2]) / 3;
		ascii += lum > 150 ? '#' : lum > 60 ? '+' : lum > 10 ? '.' : ' ';
	}
	ascii += '\n';
}
console.log(ascii);

await browser.close();
