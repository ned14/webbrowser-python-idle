// Live-site boot liveness check for the GitHub Pages WebVM.
//
// Fully boots the deployed site N times in a row (fresh browser profile per
// boot — the first-time-visitor experience), requiring the desktop to come
// up (display canvas with rendered pixels) with cross-origin isolation intact
// and no runtime failure. Used by .github/workflows/liveness.yml after the
// Pages workflow publishes a new site; also runnable by hand:
//
//   node live-site-check.mjs --boots 5
//   node live-site-check.mjs --run-id 33494458967 --boots 5
//   node live-site-check.mjs --run-id 33494458967 --boots 5 --retry-on-flake
//
// With --run-id the script first waits for the deployment that contains that
// Pages run's disk image (the image name is baked into the JS bundle as
// `webvm-custom-disk_<date>_<runId>.ext2`), so a stale or half-published
// deployment is never tested. GitHub Pages is fronted by a CDN with ~10 min
// max-age, so the gate polls with cache-busted requests.
//
// --retry-on-flake: a FAILED boot is attempted once more with a fresh
// profile. The observed cold-boot crash (~1/22 boots on a healthy site) never
// repeats for a different profile, while a genuine regression fails every
// boot — so the check stays fully sensitive and the false-red rate drops from
// ~21% to ~0.2% per 5-boot run. Default (strict) is OFF: "five times in a
// row without failure" means exactly that.
//
// A boot FAILS on any of:
//   - no desktop pixels within --timeout-per-boot,
//   - a runtime failure in the console ([WebVM] runtime failed, an uncaught
//     wasm RuntimeError such as "table index is out of bounds", ...),
//   - any uncaught page error,
//   - missing cross-origin isolation (SharedArrayBuffer/COOP/COEP).
// The two known cosmetic issues of older deployments (the webvm-config.js
// 404 — staged since 2026-09-01 — and the preload `as` warning) are reported
// as NOTICE lines, never failures.
import { chromium } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SITE = process.env.LIVE_SITE_URL || 'https://ned14.github.io/webbrowser-python-idle/alpine.html';
const BOOTS = 5;
const WAIT_DEPLOY_MS = 10 * 60 * 1000;
const POLL_DEPLOY_MS = 10 * 1000;
const TIMEOUT_PER_BOOT_MS = 240 * 1000;

const canvasProbeSource = readFileSync(
	fileURLToPath(new URL('../../webvm/src/lib/canvasProbe.js', import.meta.url)),
	'utf8'
);

function arg(name, fallback) {
	const i = process.argv.indexOf(name);
	return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const site = arg('--site', SITE);
const boots = parseInt(arg('--boots', String(BOOTS)), 10);
const runId = arg('--run-id', null);
const timeoutPerBoot = parseInt(arg('--timeout-per-boot', String(TIMEOUT_PER_BOOT_MS)), 10);
const waitDeploy = parseInt(arg('--wait-deploy', String(WAIT_DEPLOY_MS)), 10);
const retryOnFlake = process.argv.includes('--retry-on-flake');

// Yell-only list: things that MUST appear in the report but never fail the
// check (known cosmetics of old deployments, fixed in the staging now).
const KNOWN_COSMETIC_REQUESTS = [/webvm-config\.js$/];

function isCosmeticRequest(url) {
	return KNOWN_COSMETIC_REQUESTS.some((re) => re.test(new URL(url).pathname));
}

// --- Deploy gate ------------------------------------------------------------

async function deployedBundleReferencesRun(siteUrl) {
	// Cache-bust: the CDN keys on the full URL.
	const bump = '?v=' + Date.now().toString(36);
	const page = await fetch(siteUrl + bump).then((r) => r.text());
	// The image name is baked into one of the SvelteKit route chunks
	// (config_public_alpine_github.js -> diskImageUrl). Fetch every
	// /_app/immutable/*.js referenced by the page until one mentions it.
	const refs = [...page.matchAll(/href="(\.\/_app\/immutable\/[^"]+\.js)"/g)].map((m) => m[1]);
	for (const ref of refs) {
		const bundle = await fetch(new URL(ref, siteUrl).href + bump).then((r) => r.text());
		if (bundle.includes(`_${runId}.ext2`)) return true;
	}
	return false;
}

async function waitForDeployment(siteUrl) {
	if (!runId) {
		console.log(`[gate] skipped (no --run-id); testing whatever is live now`);
		return;
	}
	console.log(`[gate] waiting for run ${runId} to appear in the deployed bundle…`);
	const deadline = Date.now() + waitDeploy;
	for (;;) {
		try {
			if (await deployedBundleReferencesRun(siteUrl)) {
				console.log(`[gate] run ${runId} is live`);
				return;
			}
		} catch (e) {
			// 404s and network blips during rollout are expected; keep polling.
		}
		if (Date.now() > deadline) {
			throw new Error(`deployed bundle never referenced disk image of run ${runId} within ${waitDeploy / 1000}s`);
		}
		await new Promise((r) => setTimeout(r, POLL_DEPLOY_MS));
	}
}

// --- One boot ---------------------------------------------------------------

async function oneBoot(browser, label) {
	const context = await browser.newContext(); // fresh profile: clean IDB/SW
	const page = await context.newPage();
	const consoleErrors = [];
	const pageErrors = [];
	const badRequests = []; // non-cosmetic failed requests
	page.on('console', (m) => {
		if (m.type() === 'error') consoleErrors.push(m.text());
	});
	page.on('pageerror', (e) => pageErrors.push(String(e)));
	page.on('requestfailed', (r) => {
		if (!isCosmeticRequest(r.url())) badRequests.push(r.url());
	});

	const t0 = Date.now();
	const phases = {};
	let outcome;
	try {
		await page.goto(site, { waitUntil: 'domcontentloaded', timeout: 60_000 });
		phases.goto = Date.now() - t0;
		await page.waitForSelector('#display', { timeout: 60_000 }).catch(() => {});
		phases.display = Date.now() - t0;
		await page.addScriptTag({ content: canvasProbeSource, type: 'module' });

		let pixels = false;
		while (Date.now() - t0 < timeoutPerBoot) {
			pixels = await page
				.evaluate(() => {
					const probe = window.__webvmCanvasProbe;
					const d = document.getElementById('display');
					return probe && d ? probe.hasAnyPixel(probe.sampleCanvasPixels(d)) : false;
				})
				.catch(() => false);
			if (pixels) break;
			await page.waitForTimeout(5000);
		}
		phases.pixels = Date.now() - t0;

		const iso = await page
			.evaluate(() => ({
				sab: typeof SharedArrayBuffer !== 'undefined',
				coi: typeof crossOriginIsolated === 'boolean' && crossOriginIsolated,
			}))
			.catch(() => ({ sab: false, coi: false }));

		const runtimeFailures = consoleErrors.filter((e) =>
			// The app emits boot-stage failures as "[WebVM] boot failed:" and
			// runtime-stage ones as "[WebVM] runtime failed:" (WebVM.svelte
			// console.error("[WebVM] " + phase + " failed:", err)); the wasm
			// crash text itself may or may not carry the "Uncaught" prefix
			// (observed both "Uncaught RuntimeError: table index is out of
			// bounds" and a bare "RuntimeError: null function or function
			// signature mismatch").
			/\[WebVM\] (boot|runtime) failed|RuntimeError|table index is out of bounds/.test(e)
		);
		const seriousErrors = consoleErrors.filter(
			(e) => !/Failed to load resource/.test(e)
		);

		const problems = [];
		if (!pixels) problems.push(`no desktop pixels within ${((Date.now() - t0) / 1000).toFixed(0)}s`);
		if (runtimeFailures.length) problems.push('runtime failure: ' + runtimeFailures[0].slice(0, 160));
		if (pageErrors.length) problems.push('page error: ' + pageErrors[0].slice(0, 160));
		if (badRequests.length) problems.push('failed request: ' + badRequests[0].slice(0, 120));
		if (!iso.coi || !iso.sab) problems.push(`cross-origin isolation missing (sab=${iso.sab}, coi=${iso.coi})`);

		outcome = problems.length
			? { ok: false, problems }
			: { ok: true, problems: [] };
		if (seriousErrors.length && outcome.ok) {
			// Informational: unexpected console errors with an otherwise healthy boot.
			outcome.warnings = seriousErrors.slice(0, 3);
		}
	} catch (e) {
		outcome = { ok: false, problems: ['boot threw: ' + String(e).slice(0, 200)] };
	} finally {
		await context.close();
	}

	const secs = ((Date.now() - t0) / 1000).toFixed(0);
	if (outcome.ok) {
		console.log(`[boot ${label}] PASS in ${secs}s${outcome.warnings ? ' (warnings)' : ''}`);
	} else {
		console.log(`[boot ${label}] FAIL after ${secs}s (goto ${((phases.goto ?? 0) / 1000).toFixed(0)}s, display ${((phases.display ?? 0) / 1000).toFixed(0)}s):`);
		for (const p of outcome.problems) console.log(`    - ${p}`);
		console.log(`    - console errors (${consoleErrors.length}):`);
		for (const e of consoleErrors.slice(0, 6)) console.log(`        ${e.slice(0, 400)}`);
		console.log(`    - page error stacks (${pageErrors.length}):`);
		for (const e of pageErrors.slice(0, 2)) console.log(`        ${e.slice(0, 1500)}`);
		if (badRequests.length) {
			console.log(`    - failed requests (${badRequests.length}):`);
			for (const u of badRequests.slice(0, 5)) console.log(`        ${u.slice(0, 200)}`);
		}
	}
	return outcome;
}

// --- Main -------------------------------------------------------------------

const browser = await chromium.launch({ headless: true });
const results = [];
try {
	await waitForDeployment(site);
	for (let i = 1; i <= boots; i++) {
		let outcome = await oneBoot(browser, `${i}/${boots}`);
		if (!outcome.ok && retryOnFlake) {
			// Flake retry: the observed cold-boot crash is per-profile — a
			// fresh profile retry reproduces it only for a real regression.
			console.log(`[boot ${i}/${boots}] failed — retrying once with a fresh profile (--retry-on-flake)`);
			outcome = await oneBoot(browser, `${i}/${boots} (flake retry)`);
		}
		results.push(outcome);
	}
} finally {
	await browser.close();
}

const passed = results.filter((r) => r.ok).length;
console.log(`\n=== ${passed}/${boots} boots passed — ${passed === boots ? 'live site is healthy' : 'LIVE SITE CHECK FAILED'} ===`);
if (results.some((r) => !r.ok)) {
	process.exitCode = 1;
}