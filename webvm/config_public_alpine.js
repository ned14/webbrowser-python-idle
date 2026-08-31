// Storage mode and image-build fingerprint, injected at build time by
// vite.config.js from the WEBVM_MODE / WEBVM_IMAGE_BUILD env vars
// (set by build.sh / CI).
export const storageBackend = __WEBVM_MODE__;
export const imageBuild = __WEBVM_IMAGE_BUILD__;
// The git commit this build was made from (and its date), injected from the
// WEBVM_COMMIT / WEBVM_COMMIT_DATE env vars (set by make/CI). Empty on an
// unversioned dev build (npm run dev / plain npm run build).
export const commit = __WEBVM_COMMIT__;
export const commitDate = __WEBVM_COMMIT_DATE__;

// The root filesystem location (served by our nginx, same origin, byte ranges).
// The ?v=<image-build> query is the CONTENT FINGERPRINT (the same value the
// cacheId embeds): it makes the browser HTTP cache key differ per image
// build, so nginx can serve the ext2 with Cache-Control immutable and repeat
// boots hit the cache with zero revalidation, while an image upgrade changes
// the URL and fetches the new image exactly then (a stale cached base can
// never be paired with a new-fingerprint overlay — the corruption the
// fingerprint exists to prevent). nginx ignores the query string and
// HttpBytesDevice carries it through its range GETs.
export const diskImageUrl = "/custom-disk-images/webvm-custom-disk.ext2?v=" + imageBuild;
// The root filesystem backend type
export const diskImageType = "bytes";
// Print an introduction message about the technology
export const printIntro = false;
// Is a graphical display needed
export const needsDisplay = true;
// Executable full path (Required)
export const cmd = "/sbin/init";
// Arguments, as an array (Required)
export const args = [];
// Optional extra parameters
export const opts = {
	// User id
	uid: 0,
	// Group id
	gid: 0
};
