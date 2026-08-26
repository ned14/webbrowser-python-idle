import { expect, test } from '../lib/browser.js';
import { waitForDesktop, waitForLightDesktop, canvasHash } from '../lib/desktop.js';

// Page-resize regression test: after the BROWSER window is resized the
// screen must keep rendering CORRECTLY and the VM must NOT hang.
//
// Behaviour under CheerpX 1.3.8 (verified against the minified core,
// plans/display-bug.md follow-up): the app's resize handler recomputes and
// calls cx.setKmsCanvas, but post-boot the core keeps its ORIGINAL backing
// geometry — later calls only re-bind input handling. The visible
// "adjustment" is therefore the browser scaling the FIXED backing store
// into the #display CSS box, which tracks the viewport minus the fixed
// 56 px sidebar (1400 -> 1344, 1050 -> 994). What MUST hold on every
// resize:
//   1. the display CSS box follows the viewport exactly;
//   2. the explorer still fills the screen with light pixels (content
//      re-renders correctly — never collapsing to the black Openbox root);
//   3. sweeping the mouse keeps CHANGING the rendered frame (a wedged
//      pipeline leaves the last frame static);
//   4. no engine fault ("Fault addr …", wasm OOB) and no fatal overlay.
//
// This is an E2E spec rather than a unit test on purpose: the resize path
// (window resize event -> setScreenSize -> cx.setKmsCanvas -> Xorg repaint)
// only exists inside the real browser + emulator pairing.

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

const SIDEBAR_PX = 56;

const RESIZES = [
	[1050, 720], // shrink
	[1520, 940], // grow past the initial viewport
	[1150, 820], // mid-size again
];

test('page resizes: screen keeps rendering correctly and the VM stays live', async ({ page }) => {
	test.setTimeout(420_000);

	const faults = [];
	page.on('console', (msg) => {
		if (/Fault addr|Fault from Inode|memory access out of bounds/.test(msg.text()))
			faults.push(msg.text().slice(0, 160));
	});
	page.on('pageerror', (err) => faults.push(String(err).slice(0, 160)));

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await waitForDesktop(page);
	await waitForLightDesktop(page);

	for (const [w, h] of RESIZES) {
		await page.setViewportSize({ width: w, height: h });

		// --- 1. The display CSS box follows the viewport exactly (the fixed
		// backing store is scaled into it): width = innerWidth - sidebar,
		// height = innerHeight.
		await expect
			.poll(
				() =>
					page.evaluate(([ew, eh, sb]) => {
						const d = document.getElementById('display');
						if (!d) return false;
						const r = d.getBoundingClientRect();
						return (
							Math.abs(r.width - (ew - sb)) <= 2 &&
							Math.abs(r.height - eh) <= 2
						);
					}, [w, h, SIDEBAR_PX]),
				{ timeout: 30_000, intervals: [400] }
			)
			.toBe(true);

		// --- 2. Content adjusts correctly: explorer still fills the screen
		// with light pixels at the new geometry.
		await expect
			.poll(() => lightRatio(page), { timeout: 45_000, intervals: [2000] })
			.toBeGreaterThan(0.35);

		// --- 3. The VM does NOT hang: sweeping the mouse must keep changing
		// the rendered frame (the guest X server draws the pointer).
		const h0 = await canvasHash(page);
		let changed = false;
		for (let i = 0; i < 14 && !changed; i++) {
			await page.mouse.move(180 + ((i * 37) % (w - 260)) + 80, 280 + (i % 5) * 35);
			await page.waitForTimeout(250);
			changed = (await canvasHash(page)) !== h0;
		}
		expect(changed, 'frame must keep updating while the pointer moves').toBeTruthy();

		// No fatal overlay may appear at any point during/after the resize.
		await expect(page.getByRole('alert')).toHaveCount(0);
	}

	expect(faults, 'no engine faults during resizes').toEqual([]);
});
