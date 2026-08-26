// Shared `test`/`expect` for the E2E suite — see the note on `browser`.
//
// The built-in Playwright `browser` fixture is WORKER-scoped: every test in
// the worker shares one Chromium process. Each spec boots a full WebVM
// session (a large WASM guest), and the reused renderer processes (same
// origin → same process pool) accumulate the sessions' WASM heaps — by the
// 4th–5th boot of a CI run the runner's memory is exhausted and the renderer
// dies ("Target crashed" / "Page crashed", 2026-08-26; the first 2–3 boots
// always survived, pointing at accumulation, not a per-boot defect). A
// TEST-scoped `browser` is launched fresh and torn down between tests, so
// every VM boot starts from a clean baseline and a crashed attempt cannot
// poison the next one.
import { test as base, expect, chromium } from '@playwright/test';

export const test = base.extend({
	browser: async ({}, use) => {
		const browser = await chromium.launch({ headless: true });
		await use(browser);
		await browser.close();
	},
});

export { expect };
