/*
 * xblock-fix.so - force the X connection socket into BLOCKING mode.
 *
 * Motivated by the Tk-under-CheerpX busy loop (plans/display-bug.md §2.3):
 * with the getsockname shim in place, tkinter gets past channel init but then
 * spins in an XSync-style flush-wait (XNoOp + XEventsQueued + XFlush), where
 * poll(fd=X, POLLIN|POLLOUT, -1) keeps returning POLLOUT (the socket is
 * genuinely writable) and recvmsg keeps returning EAGAIN (the socket was set
 * O_NONBLOCK by Xlib). A blocking socket makes Xlib's reply-wait actually
 * block on the reply instead of busy-retrying.
 *
 * Scope: ONLY the X connection fd (identified at connect() time by its AF_UNIX
 * path, e.g. /tmp/.X11-unix/X0 or the abstract equivalent). Every other fd is
 * passed through untouched, so Tcl's notifier pipe, loggers' fds, etc. keep
 * their own blocking semantics.
 *
 * Mechanism:
 *   - connect(): if the target is an AF_UNIX socket whose path looks like an
 *     X11 socket, remember the fd.
 *   - fcntl(): on that fd, strip O_NONBLOCK from F_SETFL before forwarding.
 *   - ioctl(): on that fd, neutralize FIONBIO (clear the "enable" arg) so the
 *     socket cannot be switched to non-blocking that way either.
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -shared -fPIC -o xblock-fix.so xblock-fix.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stddef.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>

static int xfd = -1;

static int looks_like_x11_socket(const struct sockaddr *addr, socklen_t len)
{
	if (!addr || addr->sa_family != AF_UNIX)
		return 0;
	if (len < (socklen_t)offsetof(struct sockaddr_un, sun_path))
		return 0;
	const struct sockaddr_un *un = (const struct sockaddr_un *)addr;
	const char *p = un->sun_path;
	size_t plen = len - offsetof(struct sockaddr_un, sun_path);
	/* abstract sockets start with a NUL; skip it when matching */
	if (plen > 0 && p[0] == '\0') {
		p++;
		plen--;
	}
	return (plen >= 5 && strncmp(p, ".X11-", 5) == 0)
	    || strstr(p, "/X11-") != NULL
	    || strstr(p, "X11-unix") != NULL;
}

int connect(int fd, const struct sockaddr *addr, socklen_t len)
{
	static int (*real)(int, const struct sockaddr *, socklen_t) = NULL;
	if (!real)
		real = (int (*)(int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "connect");
	if (looks_like_x11_socket(addr, len))
		xfd = fd;
	return real ? real(fd, addr, len) : -1;
}

int fcntl(int fd, int cmd, ...)
{
	static int (*real)(int, int, ...) = NULL;
	if (!real)
		real = (int (*)(int, int, ...))dlsym(RTLD_NEXT, "fcntl");
	va_list ap;
	va_start(ap, cmd);
	void *arg = va_arg(ap, void *);
	va_end(ap);
	if (fd == xfd && cmd == F_SETFL) {
		long flags = (long)arg & ~(long)O_NONBLOCK;
		arg = (void *)flags;
	}
	return real ? real(fd, cmd, arg) : -1;
}

int ioctl(int fd, int req, ...)
{
	static int (*real)(int, int, ...) = NULL;
	if (!real)
		real = (int (*)(int, int, ...))dlsym(RTLD_NEXT, "ioctl");
	va_list ap;
	va_start(ap, req);
	void *arg = va_arg(ap, void *);
	va_end(ap);
	if (fd == xfd && req == FIONBIO && arg) {
		int *on = (int *)arg;
		*on = 0; /* neutralize "set non-blocking" */
	}
	return real ? real(fd, req, arg) : -1;
}
