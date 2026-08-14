/*
 * inotify-off.so - force GIO to fall back to its POLLING file monitor.
 *
 * Under CheerpX, both GTK3 file-manager desktop clients (pcmanfm, spacefm)
 * deadlock during startup right after GIO directory monitors are registered
 * (plans/display-bug.md §2.9), even though raw inotify_init1/add_watch work.
 * Hypothesis: the inotify GSource's interaction with the CheerpX main loop
 * is the deadlock, not the syscalls themselves. GIO uses its polling
 * directory monitor instead when inotify_init fails, so this shim makes
 * inotify_init/inotify_init1 fail (EPERM) and neuters add_watch/rm_watch.
 * Preload it ONLY into the desktop client (i3 autostart + keep-alive), not
 * session-wide.
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -shared -fPIC -o inotify-off.so inotify-off.c -ldl
 */
#define _GNU_SOURCE
#include <sys/inotify.h>
#include <errno.h>

int inotify_init(void)
{
	errno = EPERM;
	return -1;
}

int inotify_init1(int flags)
{
	(void)flags;
	errno = EPERM;
	return -1;
}

int inotify_add_watch(int fd, const char *pathname, uint32_t mask)
{
	(void)fd;
	(void)pathname;
	(void)mask;
	errno = EPERM;
	return -1;
}

int inotify_rm_watch(int fd, int wd)
{
	(void)fd;
	(void)wd;
	errno = EPERM;
	return -1;
}
