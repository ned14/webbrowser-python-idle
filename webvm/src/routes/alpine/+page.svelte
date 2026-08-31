<script>
	import { onMount } from 'svelte';
	import WebVM from '$lib/WebVM.svelte';
	import { acquireSessionLock } from '$lib/sessionGuard.js';
	import { sharedCacheId, ephemeralCacheId } from '$lib/cacheId.js';
	import * as configObj from '/config_public_alpine';

	// Boot overlap: start the CheerpX runtime download NOW (module-cached, so
	// WebVM.svelte's later import awaits the SAME promise) instead of letting
	// the session-lock acquisition (up to ~1 s worst case: ping + settle)
	// serialize the runtime fetch behind it. The block devices still need
	// the lock's verdict (shared vs ephemeral cacheId) and are not started
	// early — only the runtime itself, which is cacheId-independent.
	import('@leaningtech/cheerpx').catch(() => {});

	// cacheId per mode (single derivation in $lib/cacheId.js):
	//   browser/samba/webdav -> blocks_alpine_<image-build> (shared overlay,
	//   versioned to the guest image so a rebuilt image starts a fresh overlay)
	//   none                 -> random per-session id (fresh overlay every load)

	let cacheId;
	let ready = false;
	let ephemeral = false;
	let lockError = null;

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

	if (configObj.storageBackend === "none") {
		cacheId = ephemeralCacheId();
		ready = true;
	} else {
		cacheId = sharedCacheId(configObj.imageBuild);
		onMount(async () => {
			try {
				const acquired = await acquireSessionLock();
				if (!acquired) {
					// Another live tab holds the shared overlay: boot an
					// ephemeral session that never writes to the shared overlay.
					ephemeral = true;
					cacheId = ephemeralCacheId();
				}
			} catch (e) {
				// The lock store failed (e.g. storage blocked/corrupt). Never
				// stall on "Acquiring session lock…" forever — boot ephemeral
				// (writes nothing shared) and say exactly why.
				console.error("session lock failed:", e);
				lockError = String((e && e.message) || e);
				ephemeral = true;
				cacheId = ephemeralCacheId();
			}
			ready = true;
		});
	}
</script>

{#if ready}
	{#if ephemeral}
		<div style="position:absolute; top:0; left:0; right:0; z-index:50; padding:8px 16px; background:#fde68a; color:#78350f; font-size:14px;">
			{#if lockError}
				Could not acquire the session lock ({lockError}) — this tab is running an ephemeral session and will not write to shared storage.
			{:else}
				A WebVM session is already active in another tab — this tab is running an ephemeral session and will not write to shared storage.
			{/if}
		</div>
	{/if}
	<WebVM configObj={configObj} {cacheId}>
		<p>Personal Linux desktop — Python 3 + IDLE. Files persist in the browser (or sync to your network backend).</p>
	</WebVM>
{:else}
	<p style="padding:16px; font-family:monospace;">Acquiring session lock…</p>
{/if}
