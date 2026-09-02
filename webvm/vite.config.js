import { sveltekit } from '@sveltejs/kit/vite';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';

// Mode plumbing (set by build.sh / CI):
//   WEBVM_MODE        — browser | samba | webdav | none (selects cacheId mode,
//                       URL-hash sync parsing, single-session guard)
//   WEBVM_IMAGE_BUILD — content-stable fingerprint of the guest image
//                       (cacheId = blocks_alpine_<fingerprint>)
//   WEBVM_DISK_IMAGE  — (GitHub Pages build only) the chunked disk image
//                       basename; when set, the alpine page uses the
//                       "github" disk device (config_public_alpine_github.js)
//   WEBVM_COMMIT      — git commit SHA this build was made from (shown in the
//                       GitHub sidebar tab so end users can identify the build)
//   WEBVM_COMMIT_DATE — date of that commit (YYYY-MM-DD)
//   WEBVM_DISK_BASE_URL — (optional) origin base for the ext2 disk-image URL
//                       (e.g. https://disk.webvm.nedprod.com). Empty = the
//                       image is served same-origin (today's behaviour). When
//                       set, the ext2 byte-range reads go to that origin
//                       instead (config_public_alpine.js) — used to split the
//                       disk image onto a host that is NOT the page origin
//                       (e.g. direct-to-origin reads bypassing the CDN). The
//                       origin nginx must answer CORS + OPTIONS preflight on
//                       the image location, and the CSP connect-src must
//                       allow the origin (server/nginx.conf.template +
//                       render-webvm-config.py).
const webvmMode = process.env.WEBVM_MODE || 'browser';
const webvmImageBuild = process.env.WEBVM_IMAGE_BUILD || 'dev';
const webvmDiskImage = process.env.WEBVM_DISK_IMAGE || '';
const webvmCommit = process.env.WEBVM_COMMIT || '';
const webvmCommitDate = process.env.WEBVM_COMMIT_DATE || '';
const webvmDiskBaseUrl = process.env.WEBVM_DISK_BASE_URL || '';

export default defineConfig({
	resolve: {
		alias: {
			// (The stock '/config_terminal' terminal page is dead: the root `/`
			// route is redirected by nginx to /alpine.html, so the alias and
			// its config were removed.)
			'/config_public_alpine': webvmDiskImage
				? 'config_public_alpine_github.js'
				: 'config_public_alpine.js',
			// Self-hosted CheerpX runtime (never the CDN wrapper)
			"@leaningtech/cheerpx": fileURLToPath(new URL('./src/lib/cheerpx.js', import.meta.url))
		}
	},
	define: {
		__WEBVM_MODE__: JSON.stringify(webvmMode),
		__WEBVM_IMAGE_BUILD__: JSON.stringify(webvmImageBuild),
		__WEBVM_DISK_IMAGE__: JSON.stringify(webvmDiskImage),
		__WEBVM_DISK_BASE_URL__: JSON.stringify(webvmDiskBaseUrl),
		__WEBVM_COMMIT__: JSON.stringify(webvmCommit),
		__WEBVM_COMMIT_DATE__: JSON.stringify(webvmCommitDate)
	},
	build: {
		target: "es2022"
	},
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{ src: 'tower.ico', dest: '' },
				{ src: 'scrollbar.css', dest: '' },
				{ src: 'login.html', dest: '' },
				{ src: 'assets/', dest: '' },
				{ src: 'documents/', dest: '' },
				{ src: 'cheerpx/', dest: '' }
			]
		})
	]
});
