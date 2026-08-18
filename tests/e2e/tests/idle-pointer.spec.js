import { expect, test } from '@playwright/test';
import { waitForDesktop, lightRatio, canvasHash } from '../lib/desktop.js';

// Regression test (plans/display-bug.md §2.11): launching IDLE from the file
// explorer must not freeze the pointer / wedge the IDLE window.
//
// Background: IDLE's default mode runs its Python shell in a subprocess over
// a 127.0.0.1 TCP loopback socket. The explorer launches IDLE through
// idle3.10-launcher, which probes loopback TCP and applies IDLE's -n
// (in-process) mode unless a COMPLETE loopback round trip works. When the
// guest's socket layer accepts binds but the inbound accept path is dead
// (the rebuilt tailscale.wasm consumes inbound SYNs — plans/networking-bug.md
// §16.9), a bind-only probe wrongly picks subprocess mode; the shell-
// subprocess handshake then hangs forever and the IpStack's SYN/SYNACK
// retransmission spin starves the guest display — the IDLE window stays
// static and, at worst, the mouse pointer (drawn by the guest X server)
// stops moving while the browser tab stays responsive. This hit `make up`
// deployments (no gateway): the baked page config wires the tailnet client,
// so guest bind(2) succeeds, but nothing can ever accept the loopback
// connect.
//
// The test drives the real user path: boot the desktop, open hello.py in
// IDLE from the file explorer, detect the explorer→IDLE screen swap, then
// require that the IDLE window is ALIVE — its shell cursor keeps blinking
// (canvas changes without mouse input) — and that the pointer keeps
// following the mouse. A wedged subprocess-mode IDLE is frozen-static and
// fails both.
//
// Note on input: under CheerpX's event pipeline a synthetic button-release
// can arrive late (desktop.spec.js note), so the open is driven as repeated
// double-clicks with menu dismissal between attempts until the swap is seen.
// The folder contents can vary per deployment (the WebDAV sync may add
// entries), so every row band is tried; a row that opens the file VIEWER
// (the explorer's non-Python opener — correct behaviour) is detected via its
// Close button and skipped, as is a directory navigation (recovered via the
// toolbar's Up button).

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

const ROW_SCAN_START_Y = 195; // below the column headings
const VIEWER_CLOSE = { x: 1240, y: 30 }; // the viewer's "✕ Close" button (top-right)
const UP_BTN = { x: 45, y: 45 }; // toolbar column 0: "Up" in navigation mode
const DARK_MARGIN = { x: 5, y: 400 }; // window edge: dismisses menus, touches nothing

// Row bands: y centers of the text rows in the file list (44px pitch).
async function rowBands(page) {
	return page.evaluate(
		({ startY }) => {
			const d = document.getElementById('display');
			const s = document.createElement('canvas');
			s.width = d.width;
			s.height = d.height;
			const c = s.getContext('2d');
			c.drawImage(d, 0, 0);
			const data = c.getImageData(0, 0, s.width, s.height).data;
			const list = [];
			let run = 0,
				runStart = 0;
			for (let y = startY; y < 520; y++) {
				let dark = 0;
				for (let x = 30; x < 220; x++) {
					const i = (y * s.width + x) * 4;
					if (data[i] < 120 && data[i + 1] < 120 && data[i + 2] < 120) dark++;
				}
				if (dark >= 3) {
					if (run === 0) runStart = y;
					run++;
				} else {
					if (run >= 3) list.push(Math.round((runStart + runStart + run) / 2));
					run = 0;
				}
			}
			if (run >= 3) list.push(Math.round((runStart + runStart + run) / 2));
			return list;
		},
		{ startY: ROW_SCAN_START_Y }
	);
}

// Watch for the explorer→app swap: light explorer -> black (withdrawn) -> light again.
async function watchForSwap(page, ms) {
	const seq = [];
	const deadline = Date.now() + ms;
	while (Date.now() < deadline) {
		const ratio = await lightRatio(page);
		seq.push(ratio > 0.3 ? 'L' : 'B');
		if (/L+B+L+/.test(seq.join(''))) return true;
		await page.waitForTimeout(200);
	}
	return false;
}

// True while the canvas keeps changing without any input (an alive IDLE
// blinks its shell cursor; a wedged one is frozen-static).
async function watchForBlink(page, ms) {
	const deadline = Date.now() + ms;
	while (Date.now() < deadline) {
		const h1 = await canvasHash(page);
		await page.waitForTimeout(3000);
		const h2 = await canvasHash(page);
		if (h1 !== h2) return true;
	}
	return false;
}

test('launching IDLE does not freeze the pointer or wedge the IDLE window', async ({ page }) => {
	test.setTimeout(540_000);

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

	// --- 1. The desktop is up: the file explorer's light window fills the
	// canvas (i3's background is black; nothing else autostarts a full-screen
	// light window).
	await waitForDesktop(page);
	await expect
		.poll(() => lightRatio(page), { timeout: 120_000, intervals: [3000] })
		.toBeGreaterThan(0.35);

	// --- 2. Sanity: the pointer follows the mouse BEFORE IDLE is launched
	// (the canvas hash must change when the mouse moves — the guest draws the
	// cursor, so this proves the display pipeline is live).
	await page.mouse.move(300, 300, { steps: 4 });
	await page.waitForTimeout(700);
	const hashA = await canvasHash(page);
	await page.mouse.move(640, 500, { steps: 4 });
	await page.waitForTimeout(700);
	const hashB = await canvasHash(page);
	expect(hashA, 'pre-IDLE baseline: pointer must follow the mouse').not.toBeNull();
	expect(hashB, 'pre-IDLE baseline: pointer must follow the mouse').not.toBeNull();
	expect(hashA, 'pre-IDLE baseline: canvas must change when the mouse moves').not.toBe(hashB);

	// The dead-accept wedge needs the guest's socket layer in its steady state
	// (the user's manual repro: launch IDLE well after the desktop settles, so
	// the tailnet client's register-retry flapping has quieted and guest
	// bind(2) succeeds). Give the boot that time before driving the launch.
	await page.waitForTimeout(120_000);

	// --- 3. Open hello.py in IDLE. Try every row band; for each, double-click
	// with retries until a swap appears. Classify the swapped app:
	//   - the file viewer (non-Python opener): its ✕ Close button returns the
	//     explorer — skip to the next row;
	//   - IDLE: nothing returns the explorer — then the blink check decides.
	let bands = await rowBands(page);
	for (let i = 0; i < 10 && bands.length === 0; i++) {
		await page.waitForTimeout(5000);
		bands = await rowBands(page); // the folder list may still be rendering
	}
	expect(bands.length, 'the file explorer must show at least one file row').toBeGreaterThan(0);

	let idleUp = false;
	for (const rowY of bands) {
		if (idleUp) break;
		let swapped = false;
		for (let r = 0; r < 3 && !swapped; r++) {
			await page.mouse.dblclick(120, rowY, { delay: 60 });
			swapped = await watchForSwap(page, 15_000);
			if (!swapped) {
				await page.mouse.click(DARK_MARGIN.x, DARK_MARGIN.y, { delay: 40 }); // dismiss any menu
				await page.waitForTimeout(1200);
			}
		}
		if (!swapped) {
			// A directory row navigates without a swap: go Up and try the next row.
			await page.mouse.click(UP_BTN.x, UP_BTN.y, { delay: 40 });
			await page.waitForTimeout(1500);
			continue;
		}
		// Swap seen. Distinguish the viewer from IDLE: the viewer closes via
		// its ✕ Close button (the explorer then reappears); IDLE has no such
		// button and stays up.
		await page.waitForTimeout(2000);
		await page.mouse.click(VIEWER_CLOSE.x, VIEWER_CLOSE.y, { delay: 40 });
		await page.waitForTimeout(4000);
		const afterClose = await lightRatio(page);
		if (afterClose > 0.85) {
			// The explorer reappeared -> it was the file viewer, not IDLE.
			continue;
		}
		// IDLE is up (the Close click did not return the explorer). The
		// definitive aliveness check: the shell cursor blinks.
		idleUp = true;
		const blinkDeadline = Date.now() + 30_000;
		let alive = false;
		while (Date.now() < blinkDeadline && !alive) {
			alive = await watchForBlink(page, 8000);
			if (!alive) {
				// A click may have opened an IDLE menu (the Close click above
				// hit the menu bar): dismiss it, then keep watching.
				await page.mouse.click(300, 120, { delay: 40 });
			}
		}
		expect(
			alive,
			'IDLE must be alive after launch: its shell cursor keeps blinking. A static ' +
				'IDLE window means the subprocess handshake is wedged (the dead-accept runtime ' +
				'hang — plans/display-bug.md §2.11); at worst the pointer freezes entirely.'
		).toBe(true);
	}
	expect(idleUp, 'IDLE must take over the screen after opening hello.py').toBe(true);

	// --- 4. The pointer must keep following the mouse for a sustained period
	// (the failure mode can go as far as the canvas stopping entirely — the
	// guest display starved by the IpStack spin).
	const moves = [
		[400, 350],
		[700, 500],
		[500, 200],
		[900, 600],
		[300, 400],
	];
	let lastChangeAt = Date.now();
	let lastHash = null;
	let moveIndex = 0;
	const pointerDeadline = Date.now() + 30_000;
	while (Date.now() < pointerDeadline) {
		const [x, y] = moves[moveIndex % moves.length];
		moveIndex++;
		await page.mouse.move(x, y, { steps: 3 });
		await page.waitForTimeout(600);
		const hash = await canvasHash(page);
		if (hash !== lastHash) lastChangeAt = Date.now();
		lastHash = hash;
		const stalled = Date.now() - lastChangeAt;
		expect(
			stalled < 10_000,
			`mouse pointer FROZE: the canvas has not changed for ${stalled / 1000}s of mouse ` +
				`movement after IDLE launched (the guest display is wedged, not the tab)`
		).toBe(true);
		await page.waitForTimeout(1500);
	}
});