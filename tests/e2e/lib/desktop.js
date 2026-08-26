// Shared desktop-boot helpers for the E2E specs (boot/desktop/network): the
// display canvas must exist and eventually contain rendered pixels, and the
// file explorer's light window must fill the canvas. Kept here so the specs
// cannot drift apart (plan §9.4).

import { expect } from '@playwright/test';

export async function waitForDesktop(page) {
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

export async function lightRatio(page) {
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

// The file explorer's light window must fill the canvas — the desktop is not
// a bare black Openbox root. Budget is 240 s, matching waitForDesktop: on a
// loaded CI runner the X session + explorer can map well after the canvas
// first shows pixels (the 120 s windows used historically failed exactly
// there — X up at 2 % light, explorer still booting, run aborted).
export async function waitForLightDesktop(page, threshold = 0.35) {
	return expect
		.poll(() => lightRatio(page), { timeout: 240_000, intervals: [3000] })
		.toBeGreaterThan(threshold);
}

// Hash of the display canvas downscaled to 256x256 (all pixels sampled, so
// even the guest-drawn mouse pointer's few pixels change the hash). Used to
// detect that the canvas KEEPS changing while the mouse moves — a frozen
// pointer means the hash stops changing.
export async function canvasHash(page) {
	return page.evaluate(() => {
		const display = document.getElementById('display');
		if (!display || !display.width || !display.height) return null;
		const scratch = document.createElement('canvas');
		scratch.width = Math.min(display.width, 256);
		scratch.height = Math.min(display.height, 256);
		const ctx = scratch.getContext('2d');
		ctx.drawImage(display, 0, 0, scratch.width, scratch.height);
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
	});
}
