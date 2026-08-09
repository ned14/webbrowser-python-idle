// Storage mode and image-build fingerprint, injected at build time by
// vite.config.js from the WEBVM_MODE / WEBVM_IMAGE_BUILD env vars
// (set by build.sh / CI).
export const storageBackend = __WEBVM_MODE__;
export const imageBuild = __WEBVM_IMAGE_BUILD__;

// The root filesystem location (served by our nginx, same origin, byte ranges)
export const diskImageUrl = "/custom-disk-images/webvm-custom-disk.ext2";
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
