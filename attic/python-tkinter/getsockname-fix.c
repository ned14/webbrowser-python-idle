/*
 * getsockname-fix.so - workaround for the CheerpX getsockname() hang.
 *
 * Root cause (see plans/display-bug.md §2.2): CheerpX 1.3.7 hangs forever
 * inside getsockname() when the fd is NOT a socket (real Linux returns
 * ENOTSOCK instantly). Tcl's channel layer calls getsockname() on each
 * standard channel (stdin/stdout/stderr) to classify it as file/tty/socket,
 * so Tcl/Tk/tkinter die at first channel creation in the guest.
 *
 * This shim intercepts getsockname(), returns ENOTSOCK immediately for any
 * fd that fstat() shows is not a socket, and forwards genuine sockets to the
 * real implementation. Behaviour is then identical to a correct kernel for
 * the channel-classification use case, and inert on real Linux (Tcl already
 * sees ENOTSOCK there).
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -shared -fPIC -o getsockname-fix.so getsockname-fix.c -ldl
 */
#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/stat.h>
#include <dlfcn.h>
#include <errno.h>
#include <stddef.h>

int getsockname(int fd, struct sockaddr *addr, socklen_t *addrlen)
{
	static int (*real)(int, struct sockaddr *, socklen_t *) = NULL;
	if (!real)
		real = (int (*)(int, struct sockaddr *, socklen_t *))dlsym(RTLD_NEXT, "getsockname");
	struct stat st;
	if (fstat(fd, &st) == 0 && !S_ISSOCK(st.st_mode)) {
		errno = ENOTSOCK;
		return -1;
	}
	if (real)
		return real(fd, addr, addrlen);
	errno = ENOSYS;
	return -1;
}
