// The site base: everything before the SvelteKit /_app/ asset dir, derived
// from this module's own URL. A GitHub Pages project site lives under a path
// (e.g. /webbrowser-python-idle/), so root-absolute asset URLs break there —
// every same-origin asset reference that cannot be made relative resolves
// through this value (the CheerpX runtime entry in cheerpx.js, the tailnet
// tun glue in network.js).
export const siteBase = (() => {
	const appDir = import.meta.url.indexOf("/_app/");
	return appDir === -1 ? "" : import.meta.url.slice(0, appDir);
})();
