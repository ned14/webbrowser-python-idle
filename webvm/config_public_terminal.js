// The root OS image location (same local Alpine desktop as the alpine page;
// nginx redirects / -> /alpine.html anyway, and this never touches a public
// disk host).
export const diskImageUrl = "/custom-disk-images/webvm-custom-disk.ext2";
// The root filesystem backend type use "cloud" for serving remotely or "bytes" for serving locally
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
