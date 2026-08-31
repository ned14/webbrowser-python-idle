// Shared desktop-boot helpers for the E2E specs (boot/desktop/network): the
// display canvas must exist and eventually contain rendered pixels, and the
// file explorer's light window must fill the canvas. Kept here so the specs
// cannot drift apart (plan §9.4).
//
// The pixel sampling itself is NOT duplicated here: webvm/src/lib/canvasProbe.js
// is the single implementation — the page's boot watchdog imports it, and this
// file injects the same source into the page (module script) so the probes run
// exactly the code the page runs.

import { expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const canvasProbeSource = readFileSync(
	fileURLToPath(new URL('../../../webvm/src/lib/canvasProbe.js', import.meta.url)),
	'utf8'
);

const probeInjected = new WeakSet();

async function ensureCanvasProbe(page) {
	if (probeInjected.has(page)) return;
	await page.addScriptTag({ content: canvasProbeSource, type: 'module' });
	probeInjected.add(page);
}

export async function waitForDesktop(page) {
	// The display canvas must exist and eventually contain rendered pixels.
	await expect(page.locator('#display')).toBeVisible({ timeout: 30_000 });
	await ensureCanvasProbe(page);
	await expect
		.poll(
			async () =>
				page.evaluate(() => {
					const probe = window.__webvmCanvasProbe;
					if (!probe) return false;
					return probe.hasAnyPixel(
						probe.sampleCanvasPixels(document.getElementById('display'))
					);
				}),
			{ timeout: 240_000, intervals: [5000] }
		)
		.toBe(true);
}

export async function lightRatio(page) {
	await ensureCanvasProbe(page);
	return page.evaluate(() => {
		const probe = window.__webvmCanvasProbe;
		const sample = probe
			? probe.sampleCanvasPixels(document.getElementById('display'))
			: null;
		if (!sample) return 0;
		const data = sample.data;
		let light = 0;
		const total = sample.width * sample.height;
		for (let i = 0; i < data.length; i += 4) {
			const r = data[i],
				g = data[i + 1],
				b = data[i + 2];
			if (r > 150 && g > 150 && b > 150) light++;
		}
		return light / total;
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
	await ensureCanvasProbe(page);
	return page.evaluate(() => {
		const probe = window.__webvmCanvasProbe;
		const sample = probe
			? probe.sampleCanvasPixels(document.getElementById('display'))
			: null;
		if (!sample) return null;
		const data = sample.data;
		let hash = 0;
		for (let i = 0; i < data.length; i += 4) {
			hash = (hash * 31 + data[i] + data[i + 1] + data[i + 2]) | 0;
		}
		return hash;
	});
}
