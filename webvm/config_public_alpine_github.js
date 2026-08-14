// Storage mode and image-build fingerprint, injected at build time by
// vite.config.js from the WEBVM_MODE / WEBVM_IMAGE_BUILD env vars. This is the
// GitHub Pages build: the disk image is deployed as 128 KiB chunks
// (<diskImageUrl>.c<hex6>.txt) plus a <diskImageUrl>.meta size file and
// streamed by CheerpX's GitHubDevice (diskImageType="github"). diskImageUrl is
// the chunk basename, baked by the Pages workflow via WEBVM_DISK_IMAGE.
export const storageBackend = __WEBVM_MODE__;
export const imageBuild = __WEBVM_IMAGE_BUILD__;

// The root filesystem location (chunked image served on GitHub Pages)
export const diskImageUrl = __WEBVM_DISK_IMAGE__;
// The root filesystem backend type
export const diskImageType = "github";
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
