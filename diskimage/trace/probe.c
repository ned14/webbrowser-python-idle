/*
 * probe.c - direct libc probe for the Tcl-under-CheerpX hang.
 *
 * The Tcl channel-open success path (Tcl_FSOpenFileChannel on
 * /usr/lib/tcl8.6/tclIndex) was traced to spin right after the stdin
 * lseek(SEEK_CUR) probe, where a working run next issues ioctl(TIOCGWINSZ)
 * (musl isatty) + getsockname() on the standard channels. This probe
 * exercises EXACTLY those libc calls - no Tcl, no Tk, no Python - printing
 * an ENTER marker before and a RET marker after each call, so a hang point
 * is unambiguous in the /dev/console capture (the app is unkillable once it
 * spins; the page-side capture bounds the run).
 *
 * Also probes the std-channel types (fstat S_ISCHR/S_ISREG), and the
 * tclIndex open sequence in the exact order a working run performs it.
 *
 * Output: raw write(2) lines, prefixed PROBE<tab>. stderr is redirected to
 * /dev/console by trace-run.sh (plan §6 pattern).
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -o probe probe.c
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

/* single unbuffered write to fd 2 (console) - never stdio-buffered */
static void out(const char *fmt, ...)
{
	char buf[512];
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	if (n < 0)
		return;
	if (n >= (int)sizeof(buf))
		n = (int)sizeof(buf) - 1;
	buf[n] = '\n';
	write(2, buf, n + 1);
}

static const char *errname(int e)
{
	static char b[32];
	switch (e) {
	case 0: return "OK";
	case EINVAL: return "EINVAL";
	case ENOTTY: return "ENOTTY";
	case ENOTSOCK: return "ENOTSOCK";
	case ESPIPE: return "ESPIPE";
	case EBADF: return "EBADF";
	case ENOENT: return "ENOENT";
	case ENXIO: return "ENXIO";
	case ENODEV: return "ENODEV";
	case EIO: return "EIO";
	default: snprintf(b, sizeof(b), "errno=%d", e); return b;
	}
}

#define ENTER(tag) out("PROBE\t%s ENTER", (tag))
#define RET(tag, v) out("PROBE\t%s RET=%d err=%s", (tag), (int)(v), errname(errno))

/* ------------------------------------------------------------------ */
/* individual probes                                                   */
/* ------------------------------------------------------------------ */

static void probe_lseek(const char *tag, int fd)
{
	ENTER(tag);
	errno = 0;
	off_t r = lseek(fd, 0, SEEK_CUR);
	RET(tag, r);
}

static void probe_isatty(const char *tag, int fd)
{
	ENTER(tag);
	errno = 0;
	int r = isatty(fd);
	RET(tag, r);
}

static void probe_ioctl_gwinsz(const char *tag, int fd)
{
	ENTER(tag);
	struct winsize ws;
	memset(&ws, 0, sizeof(ws));
	errno = 0;
	int r = ioctl(fd, TIOCGWINSZ, &ws);
	out("PROBE\t%s RET=%d err=%s ws=%ux%u", tag, r, errname(errno),
	    (unsigned)ws.ws_row, (unsigned)ws.ws_col);
}

static void probe_getsockname(const char *tag, int fd)
{
	ENTER(tag);
	struct sockaddr sa;
	socklen_t sl = sizeof(sa);
	memset(&sa, 0, sizeof(sa));
	errno = 0;
	int r = getsockname(fd, &sa, &sl);
	out("PROBE\t%s RET=%d err=%s family=%d", tag, r, errname(errno),
	    (int)sa.sa_family);
}

static void probe_fstat(const char *tag, int fd)
{
	ENTER(tag);
	struct stat st;
	memset(&st, 0, sizeof(st));
	errno = 0;
	int r = fstat(fd, &st);
	if (r == 0)
		out("PROBE\t%s RET=0 err=OK mode=%o chr=%d reg=%d fifo=%d sock=%d dev=%d blk=%d rdev=%lu size=%ld",
		    tag, st.st_mode & 0177777,
		    S_ISCHR(st.st_mode), S_ISREG(st.st_mode), S_ISFIFO(st.st_mode),
		    S_ISSOCK(st.st_mode), S_ISDIR(st.st_mode), S_ISBLK(st.st_mode),
		    (unsigned long)st.st_rdev, (long)st.st_size);
	else
		RET(tag, r);
}

/* ------------------------------------------------------------------ */
/* std-channel sweep                                                    */
/* ------------------------------------------------------------------ */

static void sweep_std(const char *label, int fd)
{
	char tag[64];
	out("PROBE\t=== %s (fd=%d) ===", label, fd);
	snprintf(tag, sizeof(tag), "%s:lseek(SEEK_CUR)", label);
	probe_lseek(tag, fd);
	snprintf(tag, sizeof(tag), "%s:isatty()", label);
	probe_isatty(tag, fd);
	snprintf(tag, sizeof(tag), "%s:ioctl(TIOCGWINSZ)", label);
	probe_ioctl_gwinsz(tag, fd);
	snprintf(tag, sizeof(tag), "%s:fstat()", label);
	probe_fstat(tag, fd);
	snprintf(tag, sizeof(tag), "%s:getsockname()", label);
	probe_getsockname(tag, fd);
}

/* ------------------------------------------------------------------ */
/* control: getsockname on a REAL socket must work (isolates the       */
/* non-socket-fd path that the std-channel probe hangs on)             */
/* ------------------------------------------------------------------ */

static void probe_socket_getsockname(void)
{
	out("PROBE\t=== control: getsockname on a real AF_UNIX socket ===");
	ENTER("socket(AF_UNIX,SOCK_STREAM)");
	errno = 0;
	int fd = socket(AF_UNIX, SOCK_STREAM, 0);
	RET("socket(AF_UNIX)", fd);
	if (fd < 0)
		return;
	ENTER("getsockname(socket)");
	{
		struct sockaddr sa;
		socklen_t sl = sizeof(sa);
		memset(&sa, 0, sizeof(sa));
		errno = 0;
		int r = getsockname(fd, &sa, &sl);
		out("PROBE\tgetsockname(socket) RET=%d err=%s family=%d", r,
		    errname(errno), (int)sa.sa_family);
	}
	close(fd);
}

/* ------------------------------------------------------------------ */
/* tclIndex open sequence (mirrors the working run's syscall order)    */
/* ------------------------------------------------------------------ */

static void probe_tclindex_open(void)
{
	const char *path = "/usr/lib/tcl8.6/tclIndex";
	out("PROBE\t=== tclIndex open sequence ===");

	ENTER("open(tclIndex)");
	errno = 0;
	int fd = open(path, O_RDONLY);
	RET("open(tclIndex)", fd);
	if (fd < 0) {
		out("PROBE\topen failed; aborting sequence");
		return;
	}

	ENTER("fcntl(F_SETFD)");
	errno = 0;
	int r = fcntl(fd, F_SETFD, 1);
	RET("fcntl(F_SETFD)", r);

	ENTER("ioctl(fd,TIOCGWINSZ) [isatty probe]");
	{
		struct winsize ws;
		memset(&ws, 0, sizeof(ws));
		errno = 0;
		r = ioctl(fd, TIOCGWINSZ, &ws);
		out("PROBE\tioctl(fd,TIOCGWINSZ) RET=%d err=%s", r, errname(errno));
	}

	ENTER("lseek(fd,0,SEEK_CUR)");
	errno = 0;
	off_t off = lseek(fd, 0, SEEK_CUR);
	out("PROBE\tlseek(fd,SEEK_CUR) RET=%ld err=%s", (long)off, errname(errno));

	ENTER("lseek(stdin,0,SEEK_CUR)");
	errno = 0;
	off = lseek(0, 0, SEEK_CUR);
	out("PROBE\tlseek(stdin,SEEK_CUR) RET=%ld err=%s", (long)off, errname(errno));

	ENTER("ioctl(stdin,TIOCGWINSZ)");
	{
		struct winsize ws;
		memset(&ws, 0, sizeof(ws));
		errno = 0;
		r = ioctl(0, TIOCGWINSZ, &ws);
		out("PROBE\tioctl(stdin,TIOCGWINSZ) RET=%d err=%s ws=%ux%u", r,
		    errname(errno), (unsigned)ws.ws_row, (unsigned)ws.ws_col);
	}

	ENTER("getsockname(stdin)");
	{
		struct sockaddr sa;
		socklen_t sl = sizeof(sa);
		memset(&sa, 0, sizeof(sa));
		errno = 0;
		r = getsockname(0, &sa, &sl);
		out("PROBE\tgetsockname(stdin) RET=%d err=%s family=%d", r,
		    errname(errno), (int)sa.sa_family);
	}

	ENTER("read(fd,4096)");
	errno = 0;
	{
		char buf[4096];
		ssize_t n = read(fd, buf, sizeof(buf));
		out("PROBE\tread(fd) RET=%zd err=%s", n, errname(errno));
	}

	ENTER("close(fd)");
	errno = 0;
	r = close(fd);
	RET("close(fd)", r);
}

/* ------------------------------------------------------------------ */

int main(void)
{
	out("PROBE-START");
	out("PROBE\tgetpid=%d", (int)getpid());

	/* control first: getsockname on a real socket must work (data either way) */
	probe_socket_getsockname();

	sweep_std("stdin", 0);
	sweep_std("stdout", 1);
	sweep_std("stderr", 2);

	/* A freshly-opened regular file: isatty on it must be 0 on Linux. */
	probe_tclindex_open();

	out("PROBE-END");
	return 0;
}
