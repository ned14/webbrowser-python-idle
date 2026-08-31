// Shared canvas pixel sampling — ONE implementation for the page's boot
// watchdog (WebVM.svelte hasDisplayPixels) and the E2E pixel probes
// (tests/e2e/lib/desktop.js injects THIS file into the page and calls the
// same functions the page uses). The page keeps a REUSED scratch canvas so
// the boot-critical watchdog tick allocates nothing; the E2E helpers use the
// default 256x256 downscale, which is exactly what the page watchdog proved
// sufficient for detecting rendered output.
//
// Also attached to window (when running in a browser) so the E2E can reach
// it after injecting this file as a module script — the app bundle sets the
// same global, so both paths converge on one implementation.
export function sampleCanvasPixels(display, opts = {}) {
	const maxDim = opts.maxDim || 256;
	const scratch = opts.scratch || null;
	if (!display || !display.width || !display.height) return null;
	try {
		const canvas = scratch != null ? scratch : document.createElement("canvas");
		canvas.width = Math.min(display.width, maxDim);
		canvas.height = Math.min(display.height, maxDim);
		const ctx = canvas.getContext("2d");
		ctx.drawImage(display, 0, 0, canvas.width, canvas.height);
		const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
		return { data: data, width: canvas.width, height: canvas.height };
	} catch (e) {
		// canvas not readable yet — not an error
		return null;
	}
}

// True when any pixel is non-black (the guest has rendered something).
export function hasAnyPixel(sample) {
	if (!sample) return false;
	const data = sample.data;
	for (let i = 0; i < data.length; i += 4) {
		if (data[i] || data[i + 1] || data[i + 2]) return true;
	}
	return false;
}

if (typeof window !== "undefined") {
	window.__webvmCanvasProbe = { sampleCanvasPixels, hasAnyPixel };
}
