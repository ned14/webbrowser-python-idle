import { expect, test } from '@playwright/test';

// Desktop-boot guarantee test (real browser, plan §9.4 boot case, extended).
// Opens the browser-mode VM in a real Chromium and asserts, against the live
// X display:
//   1. The boot does NOT hang on "Starting local ..." (the local service
//      completes and the desktop comes up).
//   2. There is NO console login prompt ("login:").
//   3. The FILE EXPLORER (a stdlib Tk app, file-explorer.py) has STARTED — its
//      tiled window fills the canvas (the desktop is not a bare black i3
//      background).
//
// The full explorer behaviour — including the "Open with IDLE" swap (explorer
// withdraws, IDLE takes the screen, the explorer reappears refreshed when
// IDLE exits), every other function, and the keep-alive relaunch — is
// exercised in-guest: file-explorer-tests.py runs under Xvfb (part of
// tests/rootfs/smoke.sh), and smoke.sh additionally boots i3 under Xvfb,
// kills the explorer, and verifies the keep-alive relaunches it. This spec
// covers what only a real VM boot in a real browser can prove.
//
// NOTE: synthetic input into the guest is intentionally NOT driven here.
// Under CheerpX's event pipeline a synthetic button-release can arrive
// seconds late (firing the touch model's long-press), and the browser Meta
// key does not map to i3's Mod4, so i3 keybindings are unreachable from the
// test. Input handling is covered by the in-guest suites instead.

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

async function waitForDesktop(page) {
	// The display canvas must exist and eventually contain rendered pixels.
	await expect(page.locator('#display')).toBeVisible({ timeout: 30_000 });
	await expect
		.poll(
			async () =>
				page.evaluate(() => {
					const display = document.getElementById('display');
					if (!display || !display.width || !display.height) return false;
					try {
						const scratch = document.createElement('canvas');
						scratch.width = display.width;
						scratch.height = display.height;
						const ctx = scratch.getContext('2d');
						ctx.drawImage(display, 0, 0);
						const data = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
						for (let i = 0; i < data.length; i += 4) {
							if (data[i] || data[i + 1] || data[i + 2]) return true;
						}
					} catch (e) {
						// canvas not readable yet — keep polling
					}
					return false;
				}),
			{ timeout: 240_000, intervals: [5000] }
		)
		.toBe(true);
}

async function lightRatio(page) {
	return page.evaluate(() => {
		const display = document.getElementById('display');
		if (!display || !display.width || !display.height) return 0;
		const scratch = document.createElement('canvas');
		scratch.width = display.width;
		scratch.height = display.height;
		const ctx = scratch.getContext('2d');
		ctx.drawImage(display, 0, 0);
		try {
			const data = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
			let light = 0;
			const total = scratch.width * scratch.height;
			for (let i = 0; i < data.length; i += 4) {
				const r = data[i],
					g = data[i + 1],
					b = data[i + 2];
				if (r > 150 && g > 150 && b > 150) light++;
			}
			return light / total;
		} catch (e) {
			return 0;
		}
	});
}

async function consoleText(page) {
	return page
		.locator('#console .xterm-rows')
		.innerText()
		.catch(() => '');
}

test('boots to the file explorer: no login prompt, no boot hang', async ({ page }) => {
	test.setTimeout(360_000);

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

	// The desktop must come up — the canvas shows the X session.
	await waitForDesktop(page);

	// --- 3. The file explorer has started: its tiled window fills the canvas
	// with light pixels (i3's background is black; nothing else autostarts a
	// full-screen light window).
	await expect
		.poll(() => lightRatio(page), { timeout: 120_000, intervals: [3000] })
		.toBeGreaterThan(0.35);

	// --- 2. No login prompt, 1. no boot hang at "Starting local ...".
	// OpenRC prints " * Starting local ..." then appends its "[ ok ]" status
	// via ANSI cursor-up escapes, which the page's xterm renders on a separate
	// DOM row — so the bare "Starting local ..." line exists even on a
	// successful boot. The real check is that the boot PROGRESSED past the
	// local service: desktop.start echoes "launching the X desktop session"
	// only after the X socket is up and the session is being launched.
	const bootText = await consoleText(page);
	expect(bootText).not.toMatch(/login:\s*$/m);
	expect(bootText.trimEnd()).not.toMatch(/Starting local\s+\.\.\.\s*$/);
	expect(bootText).toMatch(/launching the X desktop session/);
});
