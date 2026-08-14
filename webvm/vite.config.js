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
const webvmMode = process.env.WEBVM_MODE || 'browser';
const webvmImageBuild = process.env.WEBVM_IMAGE_BUILD || 'dev';
const webvmDiskImage = process.env.WEBVM_DISK_IMAGE || '';

export default defineConfig({
	resolve: {
		alias: {
			'/config_terminal': 'config_public_terminal.js',
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
		__WEBVM_DISK_IMAGE__: JSON.stringify(webvmDiskImage)
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
