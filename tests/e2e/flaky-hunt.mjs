#!/usr/bin/env node
// Flaky-boot hunter v3: boots the VM in fresh contexts repeatedly. Uses
// INLINE canvas polling (proven reliable — module-injected probes get lost
// across the app's initial double navigation), captures console/pageerrors/
// reloads, and reports per-run OK(1st)/OK(retry)/FAILED/TIMEOUT.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const ITER = Number(process.argv[2] || 8);
const LABEL = process.argv[3] || 'hunt';
const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const OUTDIR = '/tmp/flaky-runs';
mkdirSync(OUTDIR, { recursive: true });

const browser = await chromium.launch({ headless: true });
const results = [];

const POLL_FN = () => {
	const d = document.getElementById('display');
	if (!d || !d.width || !d.height) return { nb: 0, light: 0, w: 0, h: 0 };
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
		return { nb: nb / data.length * 4, light: lt / data.length * 4, w: d.width, h: d.height };
	} catch (e) { return { nb: 0, light: 0, w: d.width, h: d.height }; }
};

for (let i = 1; i <= ITER; i++) {
	const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
	const page = await ctx.newPage();
	const art = { run: i, t0: Date.now(), console: [], reloads: [] };
	const t0 = Date.now();
	const ts = () => Date.now() - t0;

	page.on('console', (m) => {
		const text = m.text();
		art.console.push({ t: ts(), type: m.type(), text: text.slice(0, 400) });
	});
	page.on('pageerror', (e) => {
		art.console.push({ t: ts(), type: 'pageerror', text: String(e && e.stack || e).slice(0, 400) });
	});
	page.on('framenavigated', (f) => {
		if (f === page.mainFrame() && f.url() !== 'about:blank')
			art.reloads.push({ t: ts(), url: f.url().slice(0, 120) });
	});

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

	let outcome = 'TIMEOUT', okAt = null, failedAt = null, last = {};
	const deadline = Date.now() + 460000;
	while (Date.now() < deadline) {
		await page.waitForTimeout(1000);
		try { last = await page.evaluate(POLL_FN); } catch (e) { last = { nb: 0, light: 0 }; }
		if (last.light > 0.35) { outcome = 'OK'; okAt = Date.now() - t0; break; }
		const fatalLine = art.console.find((c) => c.type === 'error' && /\[WebVM\] (boot|runtime|terminal) failed:/.test(c.text));
		if (fatalLine && Date.now() - t0 > 5000) { outcome = 'FAILED'; failedAt = Date.now() - t0; break; }
	}

	const retries = art.reloads.filter((r) => r.t > 2000).length;
	const r = {
		run: i, outcome, okAt, failedAt, retries,
		lastConsoleErr: art.console.filter((c) => c.type === 'error').slice(0, 2).map((c) => c.text.slice(0, 120)),
		hasTrap: art.console.some((c) => c.text.includes('Unexpected exit') || c.text.includes('out of bounds')),
	};
	results.push(r);
	writeFileSync(`${OUTDIR}/run-${String(i).padStart(2, '0')}.json`, JSON.stringify(art, null, 1));
	console.log(`run ${i}: ${outcome}${okAt ? ' at ' + (okAt / 1000).toFixed(0) + 's' : ''} retries=${retries} trap=${r.hasTrap} final=${last.w}x${last.h} ${r.lastConsoleErr.length ? 'errs=[' + r.lastConsoleErr[0].slice(0, 80) + ']' : ''}`);
	await ctx.close();
}
await browser.close();

const ok1 = results.filter((r) => r.outcome === 'OK' && r.retries === 0);
const okR = results.filter((r) => r.outcome === 'OK' && r.retries > 0);
const bad = results.filter((r) => r.outcome !== 'OK');
console.log(`\n=== ${LABEL}: ${ok1.length + okR.length}/${ITER} OK (first-try ${ok1.length}, via-retry ${okR.length}, failed ${bad.length}) ===`);
for (const r of bad) console.log(`  bad run ${r.run}: ${r.outcome} ${r.lastConsoleErr[0] || ''}`.slice(0, 140));