#!/usr/bin/env node
// slow-boot-hunt.mjs — reproduce the slow-Chromebook hang: boot the VM with
// CDP network-latency throttling (the boot is disk-read-bound: ~660 x 128 KiB
// range reads, near-serial, so per-read latency translates ~1:1 into boot
// time) and report where the boot stops. A slow Chromebook is ~3-6x slower
// than a modern Mac for WASM, so the default added latency is 200 ms/read
// (~4x the localhost ~5-10 ms/read). CPU throttling alone does NOT work:
// the CheerpX guest runs in a Web Worker, which
// Emulation.setCPUThrottlingRate does not affect.
//
// Metrics per run: canvas-sized, first-non-black-pixel, light-desktop
// (file-manager window > 35 % light), and the guest console tail at the end
// (so we can see WHERE the boot stopped). Also tracks the boot pill state.
//
// Usage:
//   node slow-boot-hunt.mjs [latencyMs] [runs]
//   E2E_SITE_URL=https://webvm.nedprod.com/alpine.html node slow-boot-hunt.mjs 200 1
import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'node:fs';

const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const LATENCY = Number(process.argv[2] || 200);
const RUNS = Number(process.argv[3] || 1);
const OUTDIR = '/tmp/slow-boot-runs';
mkdirSync(OUTDIR, { recursive: true });

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

const browser = await chromium.launch({ headless: true });

for (let i = 1; i <= RUNS; i++) {
	const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true });
	const page = await ctx.newPage();
	const cdp = await ctx.newCDPSession(page);
	await cdp.send('Network.enable');
	await cdp.send('Network.emulateNetworkConditions', {
		offline: false, latency: LATENCY,
		downloadThroughput: -1, uploadThroughput: -1,
	});

	// Capture the guest console via the xterm write() monkey-patch (the
	// capture-trace.mjs pattern).
	await page.addInitScript(() => {
		window.__consoleCapture = '';
		const iv = setInterval(() => {
			const t = window.__webvmTerm;
			if (t && !t.__cap) {
				t.__cap = true;
				const ow = t.write.bind(t);
				t.write = (d) => {
					window.__consoleCapture += d instanceof Uint8Array
						? new TextDecoder().decode(d) : String(d);
					return ow(d);
				};
				clearInterval(iv);
			}
		}, 50);
	});

	const art = { run: i, latency: LATENCY, t0: Date.now(), console: [], pill: [] };
	const t0 = Date.now();
	const ts = () => Date.now() - t0;
	page.on('console', (m) => {
		art.console.push({ t: ts(), type: m.type(), text: m.text().slice(0, 300) });
	});
	page.on('pageerror', (e) => {
		art.console.push({ t: ts(), type: 'pageerror', text: String(e && e.stack || e).slice(0, 300) });
	});

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded', timeout: 120_000 });

	let sized = null, firstNzb = null, light = null, pillSeen = false, pillGone = false;
	let last = {};
	const deadline = Date.now() + 420_000; // 7 min cap
	while (Date.now() < deadline) {
		await page.waitForTimeout(2000);
		try { last = await page.evaluate(POLL_FN); } catch (e) { last = { nb: 0, light: 0 }; }
		const t = ts() / 1000;
		if (!sized && last.w > 300) sized = t;
		if (!firstNzb && last.nb > 0.01) firstNzb = t;
		if (!light && last.light > 0.35) light = t;
		// Boot pill state
		const pill = await page.evaluate(() => {
			const el = [...document.querySelectorAll('div')].find((d) => d.textContent.includes('Estimated time remaining'));
			return el ? el.textContent.trim() : null;
		}).catch(() => null);
		if (pill && !pillSeen) { pillSeen = true; art.pill.push({ t: t, state: pill }); }
		if (pillSeen && !pill && !pillGone) { pillGone = true; art.pill.push({ t: t, state: 'GONE' }); }
		if (light) {
			// keep sampling 8 s to confirm stability, then stop
			if (Date.now() - t0 > (light * 1000 + 8000)) break;
		}
	}

	// Drain the console capture (full, not just the tail — the marker
	// timing shows the kill-loop cadence)
	const consoleFull = await page.evaluate(() => {
		const c = window.__consoleCapture || '';
		window.__consoleCapture = '';
		return c;
	}).catch(() => '');

	const r = {
		run: i, latency: LATENCY,
		sized: sized ? +sized.toFixed(0) : null,
		firstNzb: firstNzb ? +firstNzb.toFixed(0) : null,
		light: light ? +light.toFixed(0) : null,
		pill: art.pill,
		consoleFull,
		consoleTail: consoleFull.slice(-6000),
		pageErrs: art.console.filter((c) => c.type === 'pageerror').slice(0, 2).map((c) => c.text.slice(0, 200)),
	};
	writeFileSync(`${OUTDIR}/run-${String(i).padStart(2, '0')}.json`, JSON.stringify(r, null, 1));
	console.log(`run ${i} (latency ${LATENCY}ms): sized=${r.sized}s firstNzb=${r.firstNzb}s light=${r.light}s pill=${JSON.stringify(art.pill)}`);
	console.log(`  console tail: ${r.consoleTail.split('\n').slice(-8).join(' | ').slice(0, 500)}`);
	await ctx.close();
}
await browser.close();
