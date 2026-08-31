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
		// Hostnames are banned (no host.docker.internal / etc.): everything
		// resolves over 127.0.0.1 / a LAN IP, so no host-resolver rules needed.
	},
	projects: [
		{
			name: 'chromium',
			use: {
				browserName: 'chromium',
				// The pinned wasm tailscale client's netmap validation breaks
				// on Chromium >= ~140 (it loops "authReconfig: netmap not yet
				// valid" and the tailnet never comes up — the network-spec
				// regression 2026-08-30; verified passing on Chromium 130).
				// package.json pins @playwright/test to 1.48.0 (Chromium 130)
				// so CI installs the working browser; on machines where the
				// installed Playwright is newer (local dev with a newer
				// Node), this override forces the 1.48-era Chromium binary
				// when it is present.
				//
				// NODE VERSION CONSTRAINT: 1.48.0's ESM loader deadlocks on
				// Node >= 24 (playwright test hangs with zero output). Run
				// the suite on Node 18-22 (package.json engines + engine-
				// strict in tests/e2e/.npmrc enforce this); the CI server
				// job pins node 22 for exactly this reason.
				...(process.env.PW_OLD_CHROMIUM
					? { executablePath: process.env.PW_OLD_CHROMIUM }
					: {}),
			},
		},
	],
	reporter: [['list']],
});
