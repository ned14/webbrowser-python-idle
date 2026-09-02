<script>
	import { onMount } from 'svelte';
	import WebVM from '$lib/WebVM.svelte';
	import { deleteOverlayDatabases } from '$lib/cjfsVersion.js';
	import { ephemeralCacheId } from '$lib/cacheId.js';
	import * as configObj from '/config_public_alpine';

	// Boot overlap: start the CheerpX runtime download NOW (module-cached, so
	// WebVM.svelte's later import awaits the SAME promise) instead of letting
	// the pre-boot overlay sweep serialize the runtime fetch behind it. The
	// block devices still wait for the sweep (see below) — only the runtime
	// itself is started early, which is cacheId-independent.
	import('@leaningtech/cheerpx').catch(() => {});

	// Overlay cacheId — EPHEMERAL PER LOAD for every backend (2026-09-02,
	// see plans/diagnose-flaky-boots.md). Reusing an IndexedDB overlay store
	// across loads makes ~50-60 % of boots die inside the CheerpX core
	// ("Unexpected exit ... memory access out of bounds" / "table index is
	// out of bounds" / silent stalls, verified content-independent: even a
	// byte-perfect store from a clean session crashes the next boot, on
	// runtimes 1.3.8 AND 1.3.9 — a core cjFS defect in the overlay read-hit
	// path). A FRESH store per load measures ~0 % failures. Consequence:
	// browser mode keeps files only for the current session (like the 'none'
	// backend); samba/webdav modes restore user files from the network
	// backend at boot, so they are unaffected.
	let cacheId;
	let ready = false;

	// GitHub Pages cannot set COOP/COEP server-side, but the WebVM needs
	// cross-origin isolation (SharedArrayBuffer). sw.js re-serves the document
	// with the headers; if we are not isolated yet (first visit to a host that
	// does not send them, e.g. GitHub Pages), register it and reload once so the
	// browser applies them. The local server already sends the headers, so the
	// worker is not registered there at all.
	onMount(() => {
		if ('serviceWorker' in navigator && !self.crossOriginIsolated) {
			// Relative: a GitHub Pages project site lives under /<repo>/.
			navigator.serviceWorker.register('sw.js').catch(() => {});
			if (!sessionStorage.getItem('webvm-coop-reload')) {
				sessionStorage.setItem('webvm-coop-reload', '1');
				navigator.serviceWorker.ready.then(() => location.reload()).catch(() => {});
			}
		}
	});

	cacheId = ephemeralCacheId();
	onMount(async () => {
		try {
			// Sweep leftover per-session overlay stores from previous loads
			// BEFORE the VM opens its fresh one (best-effort and never
			// blocking: a store still in use by another live tab stays —
			// its deleteDatabase request is blocked — and is swept by that
			// tab's next load, so growth is bounded to ~1 store per live
			// session). Scope: ONLY this app's own 'cjFS_/blocks_alpine_*'
			// family — never the runtime's generic 'cjFS_/files/' store,
			// which co-tenant CheerpX apps on a shared origin may own (see
			// deleteOverlayDatabases in $lib/cjfsVersion.js).
			await deleteOverlayDatabases();
		} catch (e) {
			// Storage blocked/corrupt: never stall the boot on the sweep —
			// a visitor with blocked storage gets a fresh (ephemeral)
			// session anyway.
			console.error("overlay sweep failed:", e);
		}
		ready = true;
	});
</script>

{#if ready}
	<WebVM configObj={configObj} {cacheId}>
		<p>Personal Linux desktop — Python 3 + IDLE. Files in the browser last for the current session (or sync to your network backend).</p>
	</WebVM>
{/if}
