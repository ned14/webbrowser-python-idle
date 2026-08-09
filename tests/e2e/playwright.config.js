import { defineConfig } from '@playwright/test';

// Runs against a booted stack (make up / make up-tailnet / CI server job).
// The site is HTTPS with a private CA — ignoreHTTPSErrors covers that.
export default defineConfig({
	testDir: './tests',
	timeout: 300_000,
	retries: 2,
	workers: 1,
	use: {
		baseURL: process.env.E2E_BASE_URL || 'https://127.0.0.1:8081',
		ignoreHTTPSErrors: true,
		headless: true,
		viewport: { width: 1400, height: 900 },
	},
	projects: [
		{
			name: 'chromium',
			use: { browserName: 'chromium' },
		},
	],
	reporter: [['list']],
});
