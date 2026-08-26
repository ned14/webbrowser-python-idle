import { expect, test } from '@playwright/test';
import { waitForDesktop, lightRatio } from '../lib/desktop.js';

// Desktop-boot guarantee test (real browser, plan §9.4 boot case, extended).
// Opens the browser-mode VM in a real Chromium and asserts, against the live
// X display:
//   1. The boot does NOT hang on "Starting local ..." (the local service
//      completes and the desktop comes up).
//   2. There is NO console login prompt ("login:").
//   3. The FILE EXPLORER (a stdlib Tk app, file-explorer.py) has STARTED — its
//      maximized window fills the canvas (the desktop is not a bare black
//      Openbox root).
//
// The full explorer behaviour — including the "Open with IDLE" swap (explorer
// disables its UI while IDLE runs and re-enables, refreshed, when IDLE exits),
// every other function, and the keep-alive relaunch — is exercised in-guest:
// file-explorer-tests.py runs under Xvfb (part of
// tests/rootfs/smoke.sh), and smoke.sh additionally boots Openbox under Xvfb,
// kills the explorer, and verifies the keep-alive relaunches it. This spec
// covers what only a real VM boot in a real browser can prove.
//
// NOTE: synthetic input into the guest is intentionally NOT driven here.
// Under CheerpX's event pipeline a synthetic button-release can arrive
// seconds late (firing the touch model's long-press), and the browser Meta
// key does not map to Openbox's Mod4, so Openbox keybindings are unreachable
// from the test. Input handling is covered by the in-guest suites instead.

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

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

	// --- 3. The file explorer has started: its maximized window fills the
	// canvas with light pixels (the Openbox root is solid black via
	// `xsetroot -solid black`, so nothing else autostarts a full-screen light
	// window).
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
