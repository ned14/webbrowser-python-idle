import { expect, test } from '@playwright/test';

// Fatal-error overlay (plan §9.4): when the VM fails to load or stops, the
// page must show EXACTLY why, on screen — never a silent blank/frozen load.
// The boot-failure path is forced deterministically via the
// webvm-test-bootfail sessionStorage hook (WebVM.svelte initCheerpX); the
// overlay must appear with the exact reason and a working Reload button.

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

test('a boot failure shows the exact reason on screen with a working Reload', async ({
	page,
	context,
}) => {
	test.setTimeout(120_000);

	// Force the failure for THIS navigation only: clear the hook once the
	// reload below happens, so the second boot proceeds normally.
	await page.addInitScript(() => {
		const armed = sessionStorage.getItem('webvm-test-bootfail');
		if (armed) {
			sessionStorage.removeItem('webvm-test-bootfail');
			return;
		}
		sessionStorage.setItem('webvm-test-bootfail', '1');
	});

	// First load: the forced boot failure must surface the overlay.
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	const alert = page.getByRole('alert');
	await expect(alert).toBeVisible({ timeout: 60_000 });
	await expect(alert).toContainText('The VM failed to start');
	// The EXACT reason (the thrown message), not a generic message.
	await expect(alert).toContainText('test-forced boot failure');

	// The Reload button must work even though boot failed before the block
	// cache existed (handleReset falls back to a plain reload).
	await alert.getByRole('button', { name: 'Reload' }).click();
	await page.waitForLoadState('domcontentloaded');
	// The hook was consumed: this load must NOT fail — the app mounts with
	// the display canvas and no fatal overlay.
	await expect(page.locator('#display')).toBeVisible({ timeout: 60_000 });
	await expect(page.getByRole('alert')).toHaveCount(0);
});
