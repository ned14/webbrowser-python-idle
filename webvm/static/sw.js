// COOP/COEP injection service worker for the GitHub Pages deployment.
// GitHub Pages cannot set these headers server-side, but the WebVM needs
// cross-origin isolation (SharedArrayBuffer) to boot. On every navigation this
// worker re-serves the document with the isolation headers; the page
// (src/routes/alpine/+page.svelte) registers it and reloads once so the
// browser applies them.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
	if (event.request.mode !== 'navigate') return;
	event.respondWith(
		(async () => {
			const response = await fetch(event.request);
			const headers = new Headers(response.headers);
			headers.set('Cross-Origin-Opener-Policy', 'same-origin');
			headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
			return new Response(response.body, {
				status: response.status,
				statusText: response.statusText,
				headers,
			});
		})(),
	);
});
