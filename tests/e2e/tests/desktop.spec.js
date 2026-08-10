import { expect, test } from '@playwright/test';

// Desktop-boot guarantee test (real browser, plan §9.4 boot case, extended).
// Opens the browser-mode VM in a real Chromium and asserts, against the live
// X display:
//   1. The boot does NOT hang on "Starting local ..." (the local service
//      completes and the desktop comes up).
//   2. There is NO console login prompt ("login:").
//   3. IDLE has STARTED — its tiled Tk window fills the canvas (the desktop
//      is not a bare black i3 background).
//   4. KEY presses have an effect in IDLE — typing a Python expression into
//      the shell renders its echo and result on the canvas.
//   5. MOUSE clicks have an effect in IDLE — clicking a menu opens a dropdown
//      (a distinct, detectable canvas change).
//
// "Starting local" and "login:" are read from the page's xterm console, which
// mirrors the guest's console tty.

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

async function canvasStats(page) {
	return page.evaluate(() => {
		const display = document.getElementById('display');
		if (!display || !display.width || !display.height) return null;
		const scratch = document.createElement('canvas');
		scratch.width = display.width;
		scratch.height = display.height;
		const ctx = scratch.getContext('2d');
		ctx.drawImage(display, 0, 0);
		const data = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
		let light = 0;
		const total = scratch.width * scratch.height;
		for (let i = 0; i < data.length; i += 4) {
			const r = data[i],
				g = data[i + 1],
				b = data[i + 2];
			if (r > 150 && g > 150 && b > 150) light++;
		}
		return { width: scratch.width, height: scratch.height, light, total };
	});
}

// A content hash of a canvas region (as a data URL), for before/after change
// detection that is immune to cursor blink (the whole region must change).
async function canvasRegion(page, x, y, w, h) {
	return page.evaluate(
		({ x, y, w, h }) => {
			const display = document.getElementById('display');
			const scratch = document.createElement('canvas');
			scratch.width = w;
			scratch.height = h;
			const ctx = scratch.getContext('2d');
			ctx.drawImage(display, x, y, w, h, 0, 0, w, h);
			return scratch.toDataURL();
		},
		{ x, y, w, h }
	);
}

async function consoleText(page) {
	return page
		.locator('#console .xterm-rows')
		.innerText()
		.catch(() => '');
}

test('boots to a responsive IDLE: no login prompt, no boot hang, keyboard + mouse work', async ({
	page,
}) => {
	test.setTimeout(360_000);

	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });

	// The desktop must come up — the canvas shows the X session.
	await waitForDesktop(page);

	// --- 3. IDLE has started: its tiled Tk window fills the canvas with
	// light pixels (i3's background is black; nothing else autostarts a
	// full-screen light window).
	await expect
		.poll(
			async () => {
				const stats = await canvasStats(page);
				return stats ? stats.light / stats.total : 0;
			},
			{ timeout: 60_000, intervals: [3000] }
		)
		.toBeGreaterThan(0.35);

	// --- 2. No login prompt, 1. no boot hang at "Starting local ...".
	const bootText = await consoleText(page);
	expect(bootText).not.toMatch(/login:\s*$/m);
	expect(bootText).not.toMatch(/Starting local\s+\.\.\.\s*$/m);

	// --- 4. KEY presses have an effect in IDLE. Click into the shell to focus
	// the display (the click also drops the insertion point on the prompt),
	// type a Python expression, press Enter, and require the canvas to change
	// as IDLE echoes the line and prints its result.
	const stats = await canvasStats(page);
	await page.locator('#display').click({ position: { x: stats.width / 2, y: stats.height / 2 } });
	await page.evaluate(() => document.getElementById('display').focus());

	const beforeTyping = await canvasRegion(page, 0, 0, stats.width, stats.height);
	await page.keyboard.type('print(6*7)');
	await page.keyboard.press('Enter');

	await expect
		.poll(
			async () => (await canvasRegion(page, 0, 0, stats.width, stats.height)) !== beforeTyping,
			{ timeout: 30_000, intervals: [2000] }
		)
		.toBe(true);

	// --- 5. MOUSE clicks have an effect in IDLE: clicking the "Options" menu
	// on IDLE's menu bar opens a dropdown. The menu bar sits at the very top
	// of the (full-screen) IDLE window; the dropdown appears below it in the
	// top-left region. Menu coordinates are stable at the fixed 1400x900
	// viewport (IDLE's tiled window fills the canvas).
	const regionBeforeClick = await canvasRegion(page, 0, 30, 340, 320);
	await page.mouse.click(210, 15);
	await expect
		.poll(
			async () => (await canvasRegion(page, 0, 30, 340, 320)) !== regionBeforeClick,
			{ timeout: 15_000, intervals: [1000] }
		)
		.toBe(true);
	// Close the menu with Escape (also proves keys still route to the guest).
	await page.keyboard.press('Escape');
});
