// A/B probe: run the suite against the OLD Chromium (130, the revision the
// wasm tailscale client was validated with) instead of the default 151.
import { test as base, expect, chromium } from '@playwright/test';

export const test = base.extend({
	browser: async ({}, use) => {
		const browser = await chromium.launch({
			headless: true,
			executablePath: process.env.PW_OLD_CHROMIUM,
		});
		await use(browser);
		await browser.close();
	},
});

export { expect };
