// Capture the CheerpX VM's console during the guest's autostarted trace run
// (/trace/trace-run.sh), and save the raw console for post-processing.
//
// The guest streams its traces to /dev/console; the page's xterm mirrors that
// console. We monkey-patch the xterm's write() from an init script to
// accumulate every byte into window.__consoleCapture (the plan's §4 pattern).
//
// The trace apps hang in tk.Tk() (unkillable from the guest), so the console
// stalls; the capture detects the stall and tears the VM down — the only
// reliable bound. The SYS/X11-prefixed lines are split in post-processing.
//
// Usage:
//   node capture-trace.mjs [outDir] [--x11-only|--syscall-only|--probe|--verify]
// --x11-only/--syscall-only match a solo-mode guest image (baked
// /trace/run-mode); otherwise the parallel image is expected. --probe
// matches the direct-libc probe run (/trace/probe, run-mode=probe) and
// waits for its ===BEGIN-PROBE=== section marker instead of the trace ones.
// --verify matches the workaround-verification runs (run-mode=verify-tclsh /
// verify-tk) and waits for ===BEGIN-VERIFY===. --xterm matches the xterm
// control run (run-mode=verify-xterm) and waits for ===BEGIN-XTERM===.

import { chromium } from 'playwright';
import { writeFileSync, mkdirSync, openSync, writeSync, closeSync } from 'node:fs';
import { join } from 'node:path';

const SITE_URL = process.env.E2E_SITE_URL || 'https://127.0.0.1:8081/alpine.html';
const args = process.argv.slice(2);
const OUT_DIR = args[0] || '.';
const X11_ONLY = args.includes('--x11-only');
const SYSCALL_ONLY = args.includes('--syscall-only');
const PROBE = args.includes('--probe');
const VERIFY = args.includes('--verify');
const XTERM = args.includes('--xterm');
mkdirSync(OUT_DIR, { recursive: true });

const POLL_MS = 1500;
const STALL_MS = 60_000;

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({
	viewport: { width: 1400, height: 900 },
	ignoreHTTPSErrors: true,
});
const page = await context.newPage();

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

console.log('opening', SITE_URL);
await page.goto(SITE_URL, { waitUntil: 'domcontentloaded', timeout: 120_000 });

const start = Date.now();
let lastLen = 0;
let lastGrowth = Date.now();
let drainedBytes = 0; // node-side running count
let tail = ''; // rolling tail used only for marker .includes() checks
let fd = openSync(join(OUT_DIR, 'cheerpx-console-raw.txt'), 'w');

async function captured() {
	// Drain the page-side capture buffer incrementally to a file (avoids
	// holding/returning a multi-hundred-MB string, which throws
	// ERR_STRING_TOO_LONG / RangeError on huge storm runs). Returns the byte
	// count plus a small rolling tail string for marker matching.
	const chunk = await page.evaluate(() => {
		const c = window.__consoleCapture || '';
		window.__consoleCapture = '';
		return c;
	});
	if (chunk) {
		writeSync(fd, chunk);
		drainedBytes += chunk.length;
		tail = (tail + chunk).slice(-65536);
	}
	return { bytes: drainedBytes, tail };
}

console.log('waiting for the trace autostart to begin...');
let sawStart = false;
while (Date.now() - start < 8 * 60 * 1000) {
	const c = await captured();
	if (c.tail.includes('TRACE-RUN-START')) {
		sawStart = true;
		break;
	}
	if (c.bytes > lastLen) {
		lastLen = c.bytes;
		lastGrowth = Date.now();
		console.log(`  [${Math.round((Date.now() - start) / 1000)}s] waiting for start... (${c.bytes} bytes)`);
	}
	await new Promise((r) => setTimeout(r, POLL_MS));
}
if (!sawStart) {
	console.log('TRACE-RUN-START never seen. Console so far:');
	console.log((await captured()).tail.slice(-4000));
	await browser.close();
	process.exit(2);
}
console.log('trace started.');

const wantedBegins = [];
if (PROBE) wantedBegins.push('===BEGIN-PROBE===');
else if (VERIFY) wantedBegins.push('===BEGIN-VERIFY===');
else if (XTERM) wantedBegins.push('===BEGIN-XTERM===');
else if (SYSCALL_ONLY) wantedBegins.push('===BEGIN-SYSCALL===');
else if (X11_ONLY) wantedBegins.push('===BEGIN-X11CALLS===');
else { wantedBegins.push('===BEGIN-SYSCALL===', '===BEGIN-X11CALLS==='); }

console.log('waiting for the trace sections to begin...');
while (Date.now() - start < 10 * 60 * 1000) {
	const c = await captured();
	if (wantedBegins.every((m) => c.tail.includes(m))) {
		console.log(`  section(s) began (${c.bytes} bytes)`);
		lastLen = c.bytes;
		lastGrowth = Date.now();
		break;
	}
	if (c.bytes > lastLen) {
		lastLen = c.bytes;
		lastGrowth = Date.now();
		console.log(`  [${Math.round((Date.now() - start) / 1000)}s] waiting for sections... (${c.bytes} bytes)`);
	}
	await new Promise((r) => setTimeout(r, POLL_MS));
}

console.log('waiting for the console to stall (the app hangs in Tk())...');
while (Date.now() - start < 8 * 60 * 1000) {
	const c = await captured();
	if (c.bytes > lastLen) {
		lastLen = c.bytes;
		lastGrowth = Date.now();
		console.log(`  [${Math.round((Date.now() - start) / 1000)}s] growing... (${c.bytes} bytes)`);
	} else if (Date.now() - lastGrowth > STALL_MS) {
		console.log(`  console stalled at ${c.bytes} bytes`);
		break;
	}
	await new Promise((r) => setTimeout(r, POLL_MS));
}

const finalBytes = (await captured()).bytes;
console.log(`captured ${finalBytes} bytes; closing file...`);
closeSync(fd);
console.log('done');
await browser.close();
