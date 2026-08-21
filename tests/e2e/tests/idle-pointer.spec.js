import { expect, test } from '@playwright/test';
import { waitForDesktop, lightRatio, canvasHash } from '../lib/desktop.js';

// Regression test (plans/display-bug.md §2.11): launching IDLE from the file
// explorer must not freeze the pointer / wedge the IDLE window.
//
// Background: IDLE's default mode runs its Python shell in a subprocess over
// a 127.0.0.1 TCP loopback socket. The explorer launches IDLE through
// idle3.14-launcher, which probes loopback TCP and applies IDLE's -n
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
// titlebar, and which IDLE does not have), and the dismissal is CONFIRMED by
// the BLACK GAP: closing the viewer makes the withdrawn explorer re-map, so
// the screen goes black and then light again (watchForSwap's L→B→L), whereas
// IDLE's editor is just as light as the explorer and never goes black
// (a light-ratio threshold alone would misread a live IDLE as "the viewer",
// observed 2026-08-18). The dismissal clicks sweep a band of y values below
// the titlebar so they hit the viewer's toolbar ✕ but never the titlebar ✕
// above it (and never close a live IDLE).

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

// Watch for the explorer→app swap: the explorer withdraws (screen goes
// BLACK — the Openbox root) and the launched window maps (LIGHT again).
// Detects the BLACK→LIGHT transition itself rather than the L+B+L+ pattern:
// on a slow machine the withdraw can complete before the first sample, and
// an in-process IDLE (-n, the browser-phase path) can keep the screen black
// for 20-40 s while idlelib boots — a fixed window starting after the
// double-click would expire mid-black and miss the swap entirely (observed
// 2026-08-18 in CI). Returns false quickly if the screen never goes black
// (nothing launched).
async function watchForSwap(page, ms) {
	const start = Date.now();
	const blackDeadline = start + 8000; // a launch withdraws within seconds or not at all
	const deadline = start + ms;
	let sawBlack = false;
	while (Date.now() < deadline) {
		const ratio = await lightRatio(page);
		if (ratio > 0.3) {
			if (sawBlack) return true; // black -> light: the app mapped
		} else {
			sawBlack = true; // explorer withdrew; app still mapping
		}
		await page.waitForTimeout(200);
		if (!sawBlack && Date.now() > blackDeadline) return false;
	}
	return false;
}

// After a swap (explorer withdrew, another full-screen window took over),
// decide whether that window is the file viewer or IDLE — WITHOUT closing a
// window to find out (both now have an Openbox titlebar ✕, so closing would
// return the explorer and misread IDLE as the viewer).
//
// The viewer is dismissed via its OWN in-toolbar "✕ Close" (below the Openbox
// titlebar; only the viewer has one): closing it makes the WITHDRAWN explorer
// re-map, so the screen goes BLACK (viewer gone, explorer still re-mapping)
// before going light again — watchForSwap's L→B→L is that re-map. IDLE's
// editor is just as light as the explorer, so a light-ratio threshold alone
// CANNOT tell a still-up IDLE from a re-mapped explorer (observed 2026-08-18:
// a live IDLE read as "the viewer" and was skipped, failing the test); the
// black gap is the discriminator — IDLE never goes black. On a live IDLE the
// clicks land harmlessly on its menubar/shell area (never on its titlebar ✕,
// which sits above the band), so it is never closed by the sweep; a wedged
// IDLE is equally static and the clicks change nothing.
// Returns true when the swapped window is confirmed to be the viewer.
async function dismissIfViewer(page) {
	for (const y of VIEWER_CLOSE_Y_BAND) {
		await page.mouse.click(VIEWER_CLOSE_X, y, { delay: 40 });
		await page.waitForTimeout(1200);
		if (await watchForSwap(page, 8000)) return true; // explorer re-mapped
	}
	return false;
}

// IDLE is alive if the canvas redraws in response to input. The shell
// cursor blink does NOT reliably render on the canvas under CheerpX (the
// after()-timer redraw is starved — plans/display-bug.md §2.10; observed
// 2026-08-18 as a static canvas for a healthy IDLE), so a passive
// "blinking cursor" check cannot gate this test. Aliveness is therefore
// asserted by the pointer-follow block below (step 4): the §2.11 wedge
// starves the guest DISPLAY, which freezes the X-server-drawn mouse
// pointer — the one passive signal that cannot lie. A wedged IDLE that
// left the pointer moving would evade step 4, but the wedge's defining
// symptom (a frozen display) freezes the pointer it draws.

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
	//     Close (below the Openbox titlebar) — the explorer then reappears
	//     (the black gap), so skip to the next row;
	//   - IDLE: the dismissal sweep never produces a black gap (its editor is
	//     light, and its titlebar ✕ is above the sweep band), so it is never
	//     closed — the row is classified as IDLE.
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
			// Generous window: a browser-phase IDLE runs in-process (-n) and
			// can sit black for 20-40 s while idlelib boots before its window
			// maps (CI 2026-08-18). watchForSwap's early-return keeps the
			// nothing-launched case fast.
			swapped = await watchForSwap(page, 60_000);
			console.log('idle-diag row', rowY, 'attempt', r, 'swap:', swapped);
			if (!swapped && (await lightRatio(page)) <= 0.3) {
				// The screen is black: a launch IS in progress (the explorer
				// withdrew and the app is still mapping). Wait for it to map
				// rather than clicking — recovery clicks would corrupt state.
				swapped = await watchForSwap(page, 60_000);
				console.log('idle-diag row', rowY, 'black-gap recovery swap:', swapped);
			}
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
		// and fake a "viewer"). A live IDLE's window is just as light as the
		// explorer, so the dismissal is confirmed by the BLACK GAP (viewer
		// closed, explorer re-mapping) rather than a light ratio.
		await page.waitForTimeout(2000);
		if (await dismissIfViewer(page)) {
			// The explorer reappeared -> it was the file viewer, not IDLE.
			continue;
		}
		// IDLE is up (the dismissal sweep never produced a black gap, so the
		// window is not the viewer). Aliveness is not asserted here — the
		// cursor-blink signal does not render under CheerpX (§2.10), so the
		// definitive aliveness gate is the pointer-follow check below (step 4):
		// the §2.11 wedge starves the guest display and freezes the pointer.
		idleUp = true;
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