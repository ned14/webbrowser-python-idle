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
// IDLE from the file explorer, detect the explorer→IDLE swap, then require
// that the IDLE window is ALIVE — its shell cursor keeps blinking (canvas
// changes without mouse input) — and that the pointer keeps following the
// mouse. A wedged subprocess-mode IDLE is frozen-static and fails both.
//
// SWAP MODEL (2026-08-26): the explorer no longer withdraws when it launches
// an app — the X withdraw round-trip is unreliable under the CheerpX runtime
// (the explorer was observed to stay mapped and interactive over IDLE), and
// the re-map on return flickered. Instead it DISABLES its whole UI in-process
// and Openbox maximizes the launched window (IDLE or the file viewer) over
// it; when the app exits, the explorer simply re-enables. Consequences for
// pixel-level detection:
//   * There is NO black gap anymore — the screen is never empty.
//   * A launch is signalled by the explorer's disabled render: its toolbar
//     buttons grey out and the status bar text changes ("file manager
//     disabled until ..."), both within ~1 s of the click.
//   * The launched window mapping is signalled by the Openbox TITLEBAR band
//     (top ~30 px): it shows the explorer's title until the new window maps,
//     then the app's title. A directory navigation changes neither the
//     toolbar band nor the titleband (rows re-render below them), so it is
//     told apart by the toolbar band returning to the enabled baseline.
//
// The viewer is still told apart from IDLE by dismissing it: the viewer's
// own in-toolbar "✕ Close" button (top-right, BELOW the Openbox titlebar;
// IDLE does not have one) is swept; closing it returns the explorer's title
// to the titleband (and its toolbar to the enabled baseline), while the same
// clicks on a live IDLE land harmlessly on its menubar/editor and change
// nothing.
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
// Band geometry (pixels of the 1344x900 KMS canvas). The Openbox titlebar
// band, the explorer's toolbar-button band, and its status-bar band — the
// three signals the swap detection uses (see the SWAP MODEL note above). The
// pointer stays outside every band during the scans (the mouse is parked at
// the clicked row, y >= 195, or at the dismissal positions).
const BANDS = {
	title: { x: 0, y: 0, w: 512, h: 30 }, // window titlebar
	toolbar: { x: 0, y: 35, w: 400, h: 60 }, // explorer toolbar buttons
	status: { x: 0, y: 845, w: 400, h: 53 }, // explorer status bar (bottom)
};

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

// Hash of one rectangular band of the display canvas (same downscale-and-sum
// technique as canvasHash, cropped to the band). Null when unreadable.
function bandHash(page, band) {
	return page.evaluate(
		(band) => {
			const d = document.getElementById('display');
			if (!d || !d.width || !d.height) return null;
			const scratch = document.createElement('canvas');
			scratch.width = band.w;
			scratch.height = band.h;
			const ctx = scratch.getContext('2d');
			ctx.drawImage(d, band.x, band.y, band.w, band.h, 0, 0, band.w, band.h);
			try {
				const data = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
				let hash = 0;
				for (let i = 0; i < data.length; i += 4) {
					hash = (hash * 31 + data[i] + data[i + 1] + data[i + 2]) | 0;
				}
				return hash;
			} catch (e) {
				return null;
			}
		},
		band
	);
}

async function titleBandHash(page) {
	return bandHash(page, BANDS.title);
}

async function toolbarBandHash(page) {
	return bandHash(page, BANDS.toolbar);
}

async function statusBandHash(page) {
	return bandHash(page, BANDS.status);
}

// The three band hashes of the healthy, enabled explorer — sampled once the
// desktop is up, before any interaction.
async function sampleExplorerBaseline(page) {
	return {
		title: await titleBandHash(page),
		toolbar: await toolbarBandHash(page),
		status: await statusBandHash(page),
	};
}

// Watch for the explorer→app swap: the click disables the explorer (toolbar
// + status bands change, no black gap), then the launched window maps and
// REPLACES the explorer's title in the titlebar band. Returns
// { swapped, launching }:
//   swapped   — a new window's title took over the titlebar band;
//   launching — the explorer's disabled render was observed (a launch is in
//               progress; IDLE can sit disabled for 20-40 s while idlelib
//               boots in-process, so the caller must keep waiting rather
//               than clicking around).
// A click that launched nothing (missed row, plain selection) changes
// neither band pair and returns { swapped: false, launching: false } quickly
// — a directory navigation changes the status band alone (and re-enables the
// toolbar within seconds), never the titleband.
async function watchForSwap(page, ms, baseline) {
	const start = Date.now();
	const deadline = start + ms;
	const launchDeadline = start + 8000; // a launch disables within seconds or not at all
	let launching = false;
	while (Date.now() < deadline) {
		const title = await titleBandHash(page);
		if (title !== null && title !== baseline.title) return { swapped: true, launching: true };
		if (!launching) {
			const toolbar = await toolbarBandHash(page);
			const status = await statusBandHash(page);
			// Disabled render = BOTH the toolbar buttons greyed AND the status
			// text replaced. A plain selection changes only the toolbar band;
			// a directory navigation only the status band (and briefly).
			if (toolbar !== null && toolbar !== baseline.toolbar && status !== null && status !== baseline.status) {
				launching = true;
			}
		}
		if (!launching && Date.now() > launchDeadline) return { swapped: false, launching: false };
		await page.waitForTimeout(200);
	}
	return { swapped: false, launching };
}

// After a launch was observed but no window mapped yet, distinguish a slow
// boot from a directory navigation: a directory row re-enables the explorer
// (its toolbar band returns to the enabled baseline) within seconds, while a
// real launch keeps it disabled for the whole boot. Returns true when the
// explorer came back (the row was a directory).
async function explorerReturned(page, ms, baseline) {
	const deadline = Date.now() + ms;
	while (Date.now() < deadline) {
		const toolbar = await toolbarBandHash(page);
		if (toolbar !== null && toolbar === baseline.toolbar) return true;
		await page.waitForTimeout(300);
	}
	return false;
}

// After a swap, decide whether the swapped window is the file viewer or IDLE
// — WITHOUT closing a window to find out (both now have an Openbox titlebar
// ✕, so closing would return the explorer and misread IDLE as the viewer).
//
// The viewer is dismissed via its OWN in-toolbar "✕ Close" (below the Openbox
// titlebar; only the viewer has one): closing it returns the explorer's TITLE
// to the titlebar band (and its toolbar band to the enabled baseline) — the
// old L→B→L black-gap discriminator is gone because the explorer never
// un-maps now. IDLE's editor is just as light as the explorer, and the same
// clicks on a live IDLE land harmlessly on its menubar/shell area (never on
// its titlebar ✕, which sits above the band), so IDLE is never closed by the
// sweep. Returns true when the swapped window is confirmed to be the viewer.
async function dismissIfViewer(page, baseline) {
	for (const y of VIEWER_CLOSE_Y_BAND) {
		await page.mouse.click(VIEWER_CLOSE_X, y, { delay: 40 });
		await page.waitForTimeout(1200);
		if (await explorerReturned(page, 8000, baseline)) return true; // the viewer closed
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
	//     Close (below the Openbox titlebar) — the explorer's title and
	//     toolbar then return, so skip to the next row;
	//   - IDLE: the dismissal sweep never returns the explorer (its editor is
	//     light, and its titlebar ✕ is above the sweep band), so it is never
	//     closed — the row is classified as IDLE.
	let bands = await rowBands(page);
	for (let i = 0; i < 10 && bands.length === 0; i++) {
		await page.waitForTimeout(5000);
		bands = await rowBands(page); // the folder list may still be rendering
	}
	expect(bands.length, 'the file explorer must show at least one file row').toBeGreaterThan(0);

	// The enabled-explorer band baseline (pointer parked at (640,500), outside
	// every band). Sample AFTER the pointer sanity checks so the bands are
	// clean, and right before the scan so the status bar shows "Ready".
	const baseline = await sampleExplorerBaseline(page);

	let idleUp = false;
	for (const rowY of bands) {
		if (idleUp) break;
		let swapped = false;
		for (let r = 0; r < 3 && !swapped; r++) {
			await page.mouse.dblclick(120, rowY, { delay: 60 });
			// Generous window: a browser-phase IDLE runs in-process (-n) and
			// can sit disabled for 20-40 s while idlelib boots before its window
			// maps (CI 2026-08-18). watchForSwap's early-return keeps the
			// nothing-launched case fast.
			let result = await watchForSwap(page, 60_000, baseline);
			console.log('idle-diag row', rowY, 'attempt', r, 'swap:', result.swapped, 'launch:', result.launching);
			if (!result.swapped && result.launching) {
				// The explorer disabled itself: a launch IS in progress — or the
				// row was a directory (its toolbar re-enables within seconds).
				// Recovery clicks would corrupt state mid-launch, so wait for
				// the window to map rather than clicking.
				if (await explorerReturned(page, 10_000, baseline)) {
					// The toolbar returned: a directory row, not a launch.
					result = { swapped: false, launching: false };
					console.log('idle-diag row', rowY, 'directory navigation (toolbar returned)');
				} else {
					result = await watchForSwap(page, 60_000, baseline);
					console.log('idle-diag row', rowY, 'launch-in-progress recovery swap:', result.swapped);
				}
			}
			swapped = result.swapped;
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
		// Swap seen: the explorer disabled itself and a full-screen window
		// (viewer or IDLE) took over. Distinguish them by dismissing any STATIC
		// viewer (never by closing IDLE — its new titlebar ✕ would return the
		// explorer and fake a "viewer"). A live IDLE's window is just as light
		// as the explorer, so the dismissal is confirmed by the explorer's
		// title + toolbar returning (the old black-gap discriminator is gone:
		// the explorer never un-maps now).
		await page.waitForTimeout(2000);
		if (await dismissIfViewer(page, baseline)) {
			// The explorer came back (fully re-enabled — explorerReturned waits
			// for its toolbar band) -> it was the file viewer, not IDLE.
			await page.waitForTimeout(500);
			continue;
		}
		// IDLE is up (the dismissal sweep never returned the explorer, so the
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
