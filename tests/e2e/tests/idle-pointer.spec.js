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
//
// The desktop runs Openbox (diskimage/config/openbox/rc.xml), which draws a
// real ✕ Close button on every window titlebar and honours
// _NET_WM_ACTION_CLOSE / WM_DELETE_WINDOW. That changes how the viewer is
// told apart from IDLE after a swap: IDLE now ALSO has a clickable titlebar ✕,
// so closing it would return the explorer and make IDLE read as "the viewer".
// We therefore never close a window to identify it. Instead the viewer is
// dismissed via ITS OWN in-toolbar "✕ Close" button (which sits BELOW the
// titlebar, and which IDLE does not have), and IDLE is recognised by a STABLE,
// close-independent signal: its python-shell cursor keeps blinking (see
// dismissIfViewer below). The dismissal clicks sweep a band of y values
// below the titlebar so they hit the viewer's toolbar ✕ but never the
// titlebar ✕ above it (and never close a live IDLE).

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

const ROW_SCAN_START_Y = 195; // below the column headings
// The viewer's in-toolbar "✕ Close" button (top-right, BELOW the Openbox
// titlebar; the viewer alone has one — IDLE does not). Openbox's titlebar is
// ~24-26px (taller than i3's ~18px), so the viewer's own toolbar sits below
// it and its exact height varies with the theme/font, so the dismissal sweeps
// a band of y values rather than pinning one. Every y is safely below the
// titlebar, so a live IDLE is never at risk of its titlebar ✕ being clicked.
const VIEWER_CLOSE_X = 1240;
const VIEWER_CLOSE_Y_BAND = [48, 62, 76, 90];
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

// Sustained change-without-input. This is the STABLE IDLE signal used after a
// swap: IDLE's shell cursor blinks ~once a second forever, whereas a static
// viewer redraws once (when it maps) and then sits still. `watchForBlink`
// alone would treat that one-shot redraw as "alive", so this requires a
// SECOND change 3 s after the first before declaring the window is blinking.
// An animated GIF viewer would satisfy it too, but the rows tried first in ~/
// (Readme.md, hello.py) are the viewer-text and IDLE cases, so a false IDLE
// here is unlikely and only ever causes a benign false-pass, never a failure.
async function isBlinking(page, ms) {
	const deadline = Date.now() + ms;
	while (Date.now() < deadline) {
		if (!(await watchForBlink(page, 4000))) continue;
		await page.waitForTimeout(3000);
		const h1 = await canvasHash(page);
		await page.waitForTimeout(3000);
		if (h1 !== (await canvasHash(page))) return true;
	}
	return false;
}

// After a swap (explorer withdrew, another full-screen window took over),
// decide whether that window is the file viewer or IDLE — WITHOUT closing a
// window to find out (both now have an Openbox titlebar ✕, so closing would
// return the explorer and misread IDLE as the viewer).
//
// The viewer is dismissed via its OWN in-toolbar "✕ Close" (below the Openbox
// titlebar; only the viewer has one): if the explorer reappears it was the
// viewer -> returns true. It is only ever clicked on a window that is static
// (not blinking), so a live IDLE is never clicked and therefore never closed;
// a wedged IDLE is also static and these clicks land on its menubar/shell,
// harmlessly failing to dismiss it (so it still reaches the definitive
// aliveness check and fails).
// Returns true when the swapped window is confirmed to be the viewer.
async function dismissIfViewer(page) {
	if (await isBlinking(page, 8000)) return false; // alive -> belongs to IDLE
	for (const y of VIEWER_CLOSE_Y_BAND) {
		await page.mouse.click(VIEWER_CLOSE_X, y, { delay: 40 });
		await page.waitForTimeout(3000);
		if ((await lightRatio(page)) > 0.85) return true; // explorer reappeared
	}
	return false;
}

test('launching IDLE does not freeze the pointer or wedge the IDLE window', async ({ page }) => {
	test.setTimeout(540_000);

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

	// --- 1. The desktop is up: the file explorer's light window fills the
	// canvas (Openbox's background is solid black; nothing else autostarts a
	// full-screen light window).
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
	//   - the file viewer (non-Python opener): dismissed via its in-toolbar ✕
	//     Close (below the Openbox titlebar) — the explorer then reappears, so
	//     skip to the next row;
	//   - IDLE: recognised by its blinking shell cursor (stable, close-
	//     independent — IDLE now has a titlebar ✕ too, so it can no longer be
	//     told apart by "does a close click return the explorer").
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
		// Swap seen: the explorer withdrew and a full-screen window (viewer or
		// IDLE) took over. Distinguish them by dismissing any STATIC viewer
		// (never by closing IDLE — its new titlebar ✕ would return the explorer
		// and fake a "viewer"). A live IDLE blinks and is never clicked.
		await page.waitForTimeout(2000);
		if (await dismissIfViewer(page)) {
			// The explorer reappeared -> it was the file viewer, not IDLE.
			continue;
		}
		// IDLE is up (either its cursor is blinking, or — for a wedged IDLE —
		// the dismissal sweep harmlessly failed to bring the explorer back).
		// The definitive aliveness check: the shell cursor blinks.
		idleUp = true;
		const blinkDeadline = Date.now() + 30_000;
		let alive = false;
		while (Date.now() < blinkDeadline && !alive) {
			alive = await watchForBlink(page, 8000);
			if (!alive) {
				// A dismissal-sweep click above may have opened an IDLE menubar
				// item (on a wedged IDLE the sweep can land on the menu bar):
				// dismiss it, then keep watching.
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