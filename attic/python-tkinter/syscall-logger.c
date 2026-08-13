/*
 * syscall-logger.c - LD_PRELOAD shim that logs the application's system calls
 * at the LIBC level, for environments where ptrace/strace do not exist
 * (CheerpX: no ptrace - see plans/display-bug.md). It is the closest faithful
 * analog of `qemu -strace` available in the guest.
 *
 * Line format:  SYS<TAB><monotonic_sec.usec><TAB><pid> <func>(<args>) = <ret>
 * (CLOCK_MONOTONIC at entry; pid via getpid()).
 *
 * Real symbols are resolved with RTLD_NEXT (libc is always in the preloaded
 * library's dependency chain; RTLD_NEXT fails only for symbols outside it,
 * e.g. libX11 - those are handled by xcall-logger.c via dlopen).
 *
 * Scope (deliberate): ALL file/fs, socket, event, signal, process, time and
 * terminal syscall wrappers the app may call, so the trace covers everything
 * qemu -strace showed for a Tk startup EXCEPT the pure-allocator internals
 * (mmap/munmap/madvise/brk churn from musl malloc), which are noise for the
 * Tk-bug comparison and would dwarf the interesting calls.
 *
 * Build (i386 musl, Alpine 3.17):  gcc -O2 -shared -fPIC -o syscall-logger.so
 * syscall-logger.c -ldl
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <dlfcn.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <sys/poll.h>
#include <sys/select.h>
#include <sys/uio.h>
#include <sys/time.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <sys/sysinfo.h>
#include <sys/epoll.h>
#include <sys/utsname.h>
#include <signal.h>
#include <dirent.h>
#include <errno.h>

/* musl's _GNU_SOURCE headers map the *64 names onto the plain symbols
 * (#define open64 open, stat64 stat, ...); neutralize so both our plain and
 * *64 interposers define their own symbols. */
#undef open64
#undef openat64
#undef stat64
#undef fstat64
#undef lstat64
#undef fstatat64
#undef getrlimit64
#undef getdents64
#undef lseek64
#undef pread64
#undef pwrite64

/* musl does not declare statx(); declare it with a void* buffer (the layout
 * of struct statx is irrelevant to the logger, which only records the call). */
int statx(int dirfd, const char *path, int flags, unsigned int mask, void *buf);

/* ------------------------------------------------------------------ */
/* logging helpers                                                     */
/* ------------------------------------------------------------------ */

static void mono_ts(struct timespec *ts);

static void xlog_ts(const struct timespec *ts, const char *fmt, ...)
{
	char buf[4096];
	va_list ap;
	long pid = (long)getpid();
	int n = snprintf(buf, sizeof(buf), "SYS\t%ld.%06ld\t%ld ",
			 (long)ts->tv_sec, (long)ts->tv_nsec / 1000, pid);
	if (n < 0 || n >= (int)sizeof(buf))
		return;
	va_start(ap, fmt);
	vsnprintf(buf + n, sizeof(buf) - n, fmt, ap);
	va_end(ap);
	/* single write() per line (append the newline to the buffer) so two
	 * concurrent logger processes cannot interleave mid-line on the console */
	{
		size_t m = strlen(buf);
		if (m + 1 < sizeof(buf)) {
			buf[m] = '\n';
			buf[m + 1] = 0;
		}
	}
	fputs(buf, stderr);
}

static double now_sec(void)
{
	struct timespec ts;
	mono_ts(&ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Quote a string, truncating to 100 chars. */
static const char *qstr(const char *s)
{
	static char b[160];
	if (!s)
		return "(null)";
	int n = (int)strlen(s);
	if (n > 100)
		n = 100;
	snprintf(b, sizeof(b), "\"%.*s%s\"", n, s, strlen(s) > 100 ? "..." : "");
	return b;
}

/* ------------------------------------------------------------------ */
/* real-symbol resolution (RTLD_NEXT; libc is in scope)                */
/* ------------------------------------------------------------------ */

#define REAL(name) \
	(real_from(#name))

static void *real_from(const char *name)
{
	static void *cache[64];
	static const char *cname[64];
	static int nc = 0;
	int i;
	for (i = 0; i < nc; i++) {
		if (strcmp(cname[i], name) == 0)
			return cache[i];
	}
	void *p = dlsym(RTLD_NEXT, name);
	if (!p)
		fprintf(stderr, "SYS\t%.6f\tlogger: dlsym(RTLD_NEXT,%s): %s\n",
			now_sec(), name, dlerror());
	if (nc < 64) {
		cname[nc] = strdup(name);
		cache[nc] = p;
		nc++;
	}
	return p;
}

/* Timestamp WITHOUT going through the PLT: our own clock_gettime wrapper must
 * not be re-entered while the logger is building a line. */
static void mono_ts(struct timespec *ts)
{
	static int (*real_gt)(clockid_t, struct timespec *) = NULL;
	if (!real_gt)
		real_gt = (int(*)(clockid_t, struct timespec *))real_from("clock_gettime");
	if (real_gt) {
		real_gt(CLOCK_MONOTONIC, ts);
	} else {
		ts->tv_sec = 0;
		ts->tv_nsec = 0;
	}
}

#define TS_VAR(name) struct timespec name; mono_ts(&name)

/* ------------------------------------------------------------------ */
/* small helpers                                                       */
/* ------------------------------------------------------------------ */

static const char *open_flags(int flags)
{
	static char b[160];
	b[0] = 0;
	const char *acc = (flags & O_ACCMODE) == O_RDONLY ? "O_RDONLY"
			: (flags & O_ACCMODE) == O_WRONLY ? "O_WRONLY"
			: (flags & O_ACCMODE) == O_RDWR ? "O_RDWR" : "?";
	snprintf(b, sizeof(b), "%s", acc);
	if (flags & O_CREAT) strncat(b, "|O_CREAT", sizeof(b) - strlen(b) - 1);
	if (flags & O_EXCL) strncat(b, "|O_EXCL", sizeof(b) - strlen(b) - 1);
	if (flags & O_TRUNC) strncat(b, "|O_TRUNC", sizeof(b) - strlen(b) - 1);
	if (flags & O_APPEND) strncat(b, "|O_APPEND", sizeof(b) - strlen(b) - 1);
	if (flags & O_CLOEXEC) strncat(b, "|O_CLOEXEC", sizeof(b) - strlen(b) - 1);
#ifdef O_LARGEFILE
	if (flags & O_LARGEFILE) strncat(b, "|O_LARGEFILE", sizeof(b) - strlen(b) - 1);
#endif
	if (flags & O_NONBLOCK) strncat(b, "|O_NONBLOCK", sizeof(b) - strlen(b) - 1);
	if (flags & O_DIRECTORY) strncat(b, "|O_DIRECTORY", sizeof(b) - strlen(b) - 1);
	return b;
}

/* Decode an AF_UNIX sockaddr path (may be abstract or NUL-padded). */
static void sock_path(const struct sockaddr *sa, socklen_t len, char *out, size_t n)
{
	out[0] = 0;
	if (!sa)
		return;
	if (sa->sa_family == AF_UNIX) {
		const char *p = sa->sa_data;
		socklen_t plen = len - (socklen_t)(p - (const char *)sa);
		if (plen > 0 && p[0] == '\0') {
			/* abstract socket */
			char tmp[108];
			socklen_t k = plen - 1;
			if (k > 107) k = 107;
			memcpy(tmp, p + 1, k);
			tmp[k] = 0;
			snprintf(out, n, "AF_UNIX(abstract)\"%s\"", tmp);
		} else {
			snprintf(out, n, "AF_UNIX\"%.*s\"", (int)(plen > 107 ? 107 : plen), p);
		}
	} else {
		snprintf(out, n, "family=%d", sa->sa_family);
	}
}

static const char *sock_ret(int r)
{
	(void)r;
	return "";
}

/* ------------------------------------------------------------------ */
/* file / filesystem                                                   */
/* ------------------------------------------------------------------ */

int open(const char *path, int flags, ...)
{
	static int (*real)(const char *, int, ...) = NULL;
	if (!real)
		real = (int(*)(const char *, int, ...))REAL(open);
	va_list ap;
	va_start(ap, flags);
	mode_t mode = va_arg(ap, int);
	va_end(ap);
	TS_VAR(ts);
	int r = real ? real(path, flags, mode) : -1;
	xlog_ts(&ts, "open(%s,%s%s) = %d", qstr(path), open_flags(flags),
		(flags & O_CREAT) ? "" : "", r);
	return r;
}

int open64(const char *path, int flags, ...)
{
	static int (*real)(const char *, int, ...) = NULL;
	if (!real)
		real = (int(*)(const char *, int, ...))REAL(open64);
	va_list ap;
	va_start(ap, flags);
	mode_t mode = va_arg(ap, int);
	va_end(ap);
	TS_VAR(ts);
	int r = real ? real(path, flags, mode) : -1;
	xlog_ts(&ts, "open64(%s,%s) = %d", qstr(path), open_flags(flags), r);
	return r;
}

int openat(int dirfd, const char *path, int flags, ...)
{
	static int (*real)(int, const char *, int, ...) = NULL;
	if (!real)
		real = (int(*)(int, const char *, int, ...))REAL(openat);
	va_list ap;
	va_start(ap, flags);
	mode_t mode = va_arg(ap, int);
	va_end(ap);
	TS_VAR(ts);
	int r = real ? real(dirfd, path, flags, mode) : -1;
	xlog_ts(&ts, "openat(%d,%s,%s) = %d", dirfd, qstr(path), open_flags(flags), r);
	return r;
}

int close(int fd)
{
	static int (*real)(int) = NULL;
	if (!real)
		real = (int(*)(int))REAL(close);
	TS_VAR(ts);
	int r = real ? real(fd) : -1;
	xlog_ts(&ts, "close(%d) = %d", fd, r);
	return r;
}

ssize_t read(int fd, void *buf, size_t count)
{
	static ssize_t (*real)(int, void *, size_t) = NULL;
	if (!real)
		real = (ssize_t(*)(int, void *, size_t))REAL(read);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, buf, count) : -1;
	xlog_ts(&ts, "read(%d,%p,%zu) = %zd", fd, buf, count, r);
	return r;
}

ssize_t write(int fd, const void *buf, size_t count)
{
	static ssize_t (*real)(int, const void *, size_t) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const void *, size_t))REAL(write);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, buf, count) : -1;
	/* suppress the logger's OWN console writes (fd 2) — they are just the
	 * trace lines themselves and would double every logged call */
	if (fd != 2)
		xlog_ts(&ts, "write(%d,%p,%zu) = %zd", fd, buf, count, r);
	return r;
}

ssize_t readv(int fd, const struct iovec *iov, int iovcnt)
{
	static ssize_t (*real)(int, const struct iovec *, int) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const struct iovec *, int))REAL(readv);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, iov, iovcnt) : -1;
	xlog_ts(&ts, "readv(%d,iov=%p,cnt=%d) = %zd", fd, (void *)iov, iovcnt, r);
	return r;
}

ssize_t writev(int fd, const struct iovec *iov, int iovcnt)
{
	static ssize_t (*real)(int, const struct iovec *, int) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const struct iovec *, int))REAL(writev);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, iov, iovcnt) : -1;
	if (fd != 2)
		xlog_ts(&ts, "writev(%d,iov=%p,cnt=%d) = %zd", fd, (void *)iov, iovcnt, r);
	return r;
}

off_t lseek(int fd, off_t offset, int whence)
{
	static off_t (*real)(int, off_t, int) = NULL;
	if (!real)
		real = (off_t(*)(int, off_t, int))REAL(lseek);
	TS_VAR(ts);
	off_t r = real ? real(fd, offset, whence) : (off_t)-1;
	xlog_ts(&ts, "lseek(%d,%ld,%d) = %ld", fd, (long)offset, whence, (long)r);
	return r;
}

int stat(const char *path, struct stat *buf)
{
	static int (*real)(const char *, struct stat *) = NULL;
	if (!real)
		real = (int(*)(const char *, struct stat *))REAL(stat);
	TS_VAR(ts);
	int r = real ? real(path, buf) : -1;
	xlog_ts(&ts, "stat(%s,%p) = %d", qstr(path), (void *)buf, r);
	return r;
}

int fstat(int fd, struct stat *buf)
{
	static int (*real)(int, struct stat *) = NULL;
	if (!real)
		real = (int(*)(int, struct stat *))REAL(fstat);
	TS_VAR(ts);
	int r = real ? real(fd, buf) : -1;
	xlog_ts(&ts, "fstat(%d,%p) = %d", fd, (void *)buf, r);
	return r;
}

int lstat(const char *path, struct stat *buf)
{
	static int (*real)(const char *, struct stat *) = NULL;
	if (!real)
		real = (int(*)(const char *, struct stat *))REAL(lstat);
	TS_VAR(ts);
	int r = real ? real(path, buf) : -1;
	xlog_ts(&ts, "lstat(%s,%p) = %d", qstr(path), (void *)buf, r);
	return r;
}

int stat64(const char *path, struct stat64 *buf)
{
	static int (*real)(const char *, struct stat64 *) = NULL;
	if (!real)
		real = (int(*)(const char *, struct stat64 *))REAL(stat64);
	TS_VAR(ts);
	int r = real ? real(path, buf) : -1;
	xlog_ts(&ts, "stat64(%s,%p) = %d", qstr(path), (void *)buf, r);
	return r;
}

int fstat64(int fd, struct stat64 *buf)
{
	static int (*real)(int, struct stat64 *) = NULL;
	if (!real)
		real = (int(*)(int, struct stat64 *))REAL(fstat64);
	TS_VAR(ts);
	int r = real ? real(fd, buf) : -1;
	xlog_ts(&ts, "fstat64(%d,%p) = %d", fd, (void *)buf, r);
	return r;
}

int statx(int dirfd, const char *path, int flags, unsigned int mask, void *buf)
{
	static int (*real)(int, const char *, int, unsigned int, void *) = NULL;
	if (!real)
		real = (int(*)(int, const char *, int, unsigned int, void *))REAL(statx);
	TS_VAR(ts);
	int r = real ? real(dirfd, path, flags, mask, buf) : -1;
	xlog_ts(&ts, "statx(%d,%s,flags=0x%x,mask=0x%x) = %d", dirfd, qstr(path), flags, mask, r);
	return r;
}

int fstatat(int dirfd, const char *path, struct stat *buf, int flags)
{
	static int (*real)(int, const char *, struct stat *, int) = NULL;
	if (!real)
		real = (int(*)(int, const char *, struct stat *, int))REAL(fstatat);
	TS_VAR(ts);
	int r = real ? real(dirfd, path, buf, flags) : -1;
	xlog_ts(&ts, "fstatat(%d,%s,%p,flags=0x%x) = %d", dirfd, qstr(path), (void *)buf, flags, r);
	return r;
}

int fstatat64(int dirfd, const char *path, struct stat64 *buf, int flags)
{
	static int (*real)(int, const char *, struct stat64 *, int) = NULL;
	if (!real)
		real = (int(*)(int, const char *, struct stat64 *, int))REAL(fstatat64);
	TS_VAR(ts);
	int r = real ? real(dirfd, path, buf, flags) : -1;
	xlog_ts(&ts, "fstatat64(%d,%s,%p,flags=0x%x) = %d", dirfd, qstr(path), (void *)buf, flags, r);
	return r;
}

int access(const char *path, int mode)
{
	static int (*real)(const char *, int) = NULL;
	if (!real)
		real = (int(*)(const char *, int))REAL(access);
	TS_VAR(ts);
	int r = real ? real(path, mode) : -1;
	xlog_ts(&ts, "access(%s,mode=0x%x) = %d", qstr(path), mode, r);
	return r;
}

ssize_t readlink(const char *path, char *buf, size_t bufsiz)
{
	static ssize_t (*real)(const char *, char *, size_t) = NULL;
	if (!real)
		real = (ssize_t(*)(const char *, char *, size_t))REAL(readlink);
	TS_VAR(ts);
	ssize_t r = real ? real(path, buf, bufsiz) : -1;
	xlog_ts(&ts, "readlink(%s,%p,%zu) = %zd", qstr(path), (void *)buf, bufsiz, r);
	return r;
}

ssize_t readlinkat(int dirfd, const char *path, char *buf, size_t bufsiz)
{
	static ssize_t (*real)(int, const char *, char *, size_t) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const char *, char *, size_t))REAL(readlinkat);
	TS_VAR(ts);
	ssize_t r = real ? real(dirfd, path, buf, bufsiz) : -1;
	xlog_ts(&ts, "readlinkat(%d,%s,%p,%zu) = %zd", dirfd, qstr(path), (void *)buf, bufsiz, r);
	return r;
}

int getdents(int fd, struct dirent *dirp, size_t count)
{
	static int (*real)(int, struct dirent *, size_t) = NULL;
	if (!real)
		real = (int(*)(int, struct dirent *, size_t))REAL(getdents);
	TS_VAR(ts);
	int r = real ? real(fd, dirp, count) : -1;
	xlog_ts(&ts, "getdents(%d,%p,%zu) = %d", fd, (void *)dirp, count, r);
	return r;
}

int unlink(const char *path)
{
	static int (*real)(const char *) = NULL;
	if (!real)
		real = (int(*)(const char *))REAL(unlink);
	TS_VAR(ts);
	int r = real ? real(path) : -1;
	xlog_ts(&ts, "unlink(%s) = %d", qstr(path), r);
	return r;
}

int unlinkat(int dirfd, const char *path, int flags)
{
	static int (*real)(int, const char *, int) = NULL;
	if (!real)
		real = (int(*)(int, const char *, int))REAL(unlinkat);
	TS_VAR(ts);
	int r = real ? real(dirfd, path, flags) : -1;
	xlog_ts(&ts, "unlinkat(%d,%s,flags=0x%x) = %d", dirfd, qstr(path), flags, r);
	return r;
}

int mkdir(const char *path, mode_t mode)
{
	static int (*real)(const char *, mode_t) = NULL;
	if (!real)
		real = (int(*)(const char *, mode_t))REAL(mkdir);
	TS_VAR(ts);
	int r = real ? real(path, mode) : -1;
	xlog_ts(&ts, "mkdir(%s,mode=0%o) = %d", qstr(path), (unsigned)mode, r);
	return r;
}

int rmdir(const char *path)
{
	static int (*real)(const char *) = NULL;
	if (!real)
		real = (int(*)(const char *))REAL(rmdir);
	TS_VAR(ts);
	int r = real ? real(path) : -1;
	xlog_ts(&ts, "rmdir(%s) = %d", qstr(path), r);
	return r;
}

int rename(const char *old, const char *nw)
{
	static int (*real)(const char *, const char *) = NULL;
	if (!real)
		real = (int(*)(const char *, const char *))REAL(rename);
	TS_VAR(ts);
	int r = real ? real(old, nw) : -1;
	xlog_ts(&ts, "rename(%s,%s) = %d", qstr(old), qstr(nw), r);
	return r;
}

int chmod(const char *path, mode_t mode)
{
	static int (*real)(const char *, mode_t) = NULL;
	if (!real)
		real = (int(*)(const char *, mode_t))REAL(chmod);
	TS_VAR(ts);
	int r = real ? real(path, mode) : -1;
	xlog_ts(&ts, "chmod(%s,mode=0%o) = %d", qstr(path), (unsigned)mode, r);
	return r;
}

int fchmod(int fd, mode_t mode)
{
	static int (*real)(int, mode_t) = NULL;
	if (!real)
		real = (int(*)(int, mode_t))REAL(fchmod);
	TS_VAR(ts);
	int r = real ? real(fd, mode) : -1;
	xlog_ts(&ts, "fchmod(%d,mode=0%o) = %d", fd, (unsigned)mode, r);
	return r;
}

int chown(const char *path, uid_t owner, gid_t group)
{
	static int (*real)(const char *, uid_t, gid_t) = NULL;
	if (!real)
		real = (int(*)(const char *, uid_t, gid_t))REAL(chown);
	TS_VAR(ts);
	int r = real ? real(path, owner, group) : -1;
	xlog_ts(&ts, "chown(%s,%d,%d) = %d", qstr(path), (int)owner, (int)group, r);
	return r;
}

char *getcwd(char *buf, size_t size)
{
	static char *(*real)(char *, size_t) = NULL;
	if (!real)
		real = (char *(*)(char *, size_t))REAL(getcwd);
	TS_VAR(ts);
	char *r = real ? real(buf, size) : NULL;
	xlog_ts(&ts, "getcwd(%p,%zu) = %s", (void *)buf, size, r ? qstr(r) : "(null)");
	return r;
}

int chdir(const char *path)
{
	static int (*real)(const char *) = NULL;
	if (!real)
		real = (int(*)(const char *))REAL(chdir);
	TS_VAR(ts);
	int r = real ? real(path) : -1;
	xlog_ts(&ts, "chdir(%s) = %d", qstr(path), r);
	return r;
}

int dup(int oldfd)
{
	static int (*real)(int) = NULL;
	if (!real)
		real = (int(*)(int))REAL(dup);
	TS_VAR(ts);
	int r = real ? real(oldfd) : -1;
	xlog_ts(&ts, "dup(%d) = %d", oldfd, r);
	return r;
}

int dup2(int oldfd, int newfd)
{
	static int (*real)(int, int) = NULL;
	if (!real)
		real = (int(*)(int, int))REAL(dup2);
	TS_VAR(ts);
	int r = real ? real(oldfd, newfd) : -1;
	xlog_ts(&ts, "dup2(%d,%d) = %d", oldfd, newfd, r);
	return r;
}

int dup3(int oldfd, int newfd, int flags)
{
	static int (*real)(int, int, int) = NULL;
	if (!real)
		real = (int(*)(int, int, int))REAL(dup3);
	TS_VAR(ts);
	int r = real ? real(oldfd, newfd, flags) : -1;
	xlog_ts(&ts, "dup3(%d,%d,flags=0x%x) = %d", oldfd, newfd, flags, r);
	return r;
}

int pipe(int fds[2])
{
	static int (*real)(int[2]) = NULL;
	if (!real)
		real = (int(*)(int[2]))REAL(pipe);
	TS_VAR(ts);
	int r = real ? real(fds) : -1;
	xlog_ts(&ts, "pipe([%d,%d]) = %d", fds ? fds[0] : -1, fds ? fds[1] : -1, r);
	return r;
}

int pipe2(int fds[2], int flags)
{
	static int (*real)(int[2], int) = NULL;
	if (!real)
		real = (int(*)(int[2], int))REAL(pipe2);
	TS_VAR(ts);
	int r = real ? real(fds, flags) : -1;
	xlog_ts(&ts, "pipe2([%d,%d],flags=0x%x) = %d", fds ? fds[0] : -1, fds ? fds[1] : -1, flags, r);
	return r;
}

int fcntl(int fd, int cmd, ...)
{
	static int (*real)(int, int, ...) = NULL;
	if (!real)
		real = (int(*)(int, int, ...))REAL(fcntl);
	va_list ap;
	va_start(ap, cmd);
	void *arg = va_arg(ap, void *);
	va_end(ap);
	TS_VAR(ts);
	int r = real ? real(fd, cmd, arg) : -1;
	const char *cn = cmd == F_GETFD ? "F_GETFD" : cmd == F_SETFD ? "F_SETFD"
			: cmd == F_GETFL ? "F_GETFL" : cmd == F_SETFL ? "F_SETFL"
			: cmd == F_DUPFD ? "F_DUPFD" : cmd == F_GETLK ? "F_GETLK"
			: cmd == F_SETLK ? "F_SETLK" : cmd == F_SETLKW ? "F_SETLKW" : "?";
	xlog_ts(&ts, "fcntl(%d,%s,arg=%ld) = %d", fd, cn, (long)arg, r);
	return r;
}

int ioctl(int fd, int req, ...)
{
	static int (*real)(int, int, ...) = NULL;
	if (!real)
		real = (int(*)(int, int, ...))REAL(ioctl);
	va_list ap;
	va_start(ap, req);
	void *arg = va_arg(ap, void *);
	va_end(ap);
	TS_VAR(ts);
	int r = real ? real(fd, req, arg) : -1;
	xlog_ts(&ts, "ioctl(%d,0x%x,%p) = %d", fd, (unsigned)req, arg, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* sockets                                                             */
/* ------------------------------------------------------------------ */

int socket(int domain, int type, int protocol)
{
	static int (*real)(int, int, int) = NULL;
	if (!real)
		real = (int(*)(int, int, int))REAL(socket);
	TS_VAR(ts);
	int r = real ? real(domain, type, protocol) : -1;
	const char *dn = domain == AF_UNIX ? "AF_UNIX" : domain == AF_INET ? "AF_INET"
			: domain == AF_INET6 ? "AF_INET6" : "?";
	const char *tn = (type & 0xf) == SOCK_STREAM ? "SOCK_STREAM"
			: (type & 0xf) == SOCK_DGRAM ? "SOCK_DGRAM" : "?";
	xlog_ts(&ts, "socket(%s,%s%s,proto=%d) = %d", dn, tn,
		(type & SOCK_CLOEXEC) ? "|SOCK_CLOEXEC" : (type & SOCK_NONBLOCK) ? "|SOCK_NONBLOCK" : "",
		protocol, r);
	return r;
}

int connect(int fd, const struct sockaddr *addr, socklen_t len)
{
	static int (*real)(int, const struct sockaddr *, socklen_t) = NULL;
	if (!real)
		real = (int(*)(int, const struct sockaddr *, socklen_t))REAL(connect);
	struct timespec ts;
	char sp[256];
	sock_path(addr, len, sp, sizeof(sp));
	clock_gettime(CLOCK_MONOTONIC, &ts);
	int r = real ? real(fd, addr, len) : -1;
	xlog_ts(&ts, "connect(%d,%s,len=%u) = %d", fd, sp, (unsigned)len, r);
	return r;
}

int bind(int fd, const struct sockaddr *addr, socklen_t len)
{
	static int (*real)(int, const struct sockaddr *, socklen_t) = NULL;
	if (!real)
		real = (int(*)(int, const struct sockaddr *, socklen_t))REAL(bind);
	struct timespec ts;
	char sp[256];
	sock_path(addr, len, sp, sizeof(sp));
	clock_gettime(CLOCK_MONOTONIC, &ts);
	int r = real ? real(fd, addr, len) : -1;
	xlog_ts(&ts, "bind(%d,%s,len=%u) = %d", fd, sp, (unsigned)len, r);
	return r;
}

int listen(int fd, int backlog)
{
	static int (*real)(int, int) = NULL;
	if (!real)
		real = (int(*)(int, int))REAL(listen);
	TS_VAR(ts);
	int r = real ? real(fd, backlog) : -1;
	xlog_ts(&ts, "listen(%d,backlog=%d) = %d", fd, backlog, r);
	return r;
}

int accept(int fd, struct sockaddr *addr, socklen_t *len)
{
	static int (*real)(int, struct sockaddr *, socklen_t *) = NULL;
	if (!real)
		real = (int(*)(int, struct sockaddr *, socklen_t *))REAL(accept);
	TS_VAR(ts);
	int r = real ? real(fd, addr, len) : -1;
	xlog_ts(&ts, "accept(%d,%p,%p) = %d", fd, (void *)addr, (void *)len, r);
	return r;
}

int accept4(int fd, struct sockaddr *addr, socklen_t *len, int flags)
{
	static int (*real)(int, struct sockaddr *, socklen_t *, int) = NULL;
	if (!real)
		real = (int(*)(int, struct sockaddr *, socklen_t *, int))REAL(accept4);
	TS_VAR(ts);
	int r = real ? real(fd, addr, len, flags) : -1;
	xlog_ts(&ts, "accept4(%d,%p,%p,flags=0x%x) = %d", fd, (void *)addr, (void *)len, flags, r);
	return r;
}

int shutdown(int fd, int how)
{
	static int (*real)(int, int) = NULL;
	if (!real)
		real = (int(*)(int, int))REAL(shutdown);
	TS_VAR(ts);
	int r = real ? real(fd, how) : -1;
	xlog_ts(&ts, "shutdown(%d,how=%d) = %d", fd, how, r);
	return r;
}

int getpeername(int fd, struct sockaddr *addr, socklen_t *len)
{
	static int (*real)(int, struct sockaddr *, socklen_t *) = NULL;
	if (!real)
		real = (int(*)(int, struct sockaddr *, socklen_t *))REAL(getpeername);
	TS_VAR(ts);
	int r = real ? real(fd, addr, len) : -1;
	xlog_ts(&ts, "getpeername(%d,%p,%p) = %d", fd, (void *)addr, (void *)len, r);
	return r;
}

int getsockname(int fd, struct sockaddr *addr, socklen_t *len)
{
	static int (*real)(int, struct sockaddr *, socklen_t *) = NULL;
	if (!real)
		real = (int(*)(int, struct sockaddr *, socklen_t *))REAL(getsockname);
	TS_VAR(ts);
	int r = real ? real(fd, addr, len) : -1;
	xlog_ts(&ts, "getsockname(%d,%p,%p) = %d", fd, (void *)addr, (void *)len, r);
	return r;
}

int getsockopt(int fd, int level, int optname, void *optval, socklen_t *optlen)
{
	static int (*real)(int, int, int, void *, socklen_t *) = NULL;
	if (!real)
		real = (int(*)(int, int, int, void *, socklen_t *))REAL(getsockopt);
	TS_VAR(ts);
	int r = real ? real(fd, level, optname, optval, optlen) : -1;
	xlog_ts(&ts, "getsockopt(%d,level=%d,opt=%d,%p) = %d", fd, level, optname, optval, r);
	return r;
}

int setsockopt(int fd, int level, int optname, const void *optval, socklen_t optlen)
{
	static int (*real)(int, int, int, const void *, socklen_t) = NULL;
	if (!real)
		real = (int(*)(int, int, int, const void *, socklen_t))REAL(setsockopt);
	TS_VAR(ts);
	int r = real ? real(fd, level, optname, optval, optlen) : -1;
	xlog_ts(&ts, "setsockopt(%d,level=%d,opt=%d,%p,len=%u) = %d", fd, level, optname, optval, (unsigned)optlen, r);
	return r;
}

ssize_t send(int fd, const void *buf, size_t len, int flags)
{
	static ssize_t (*real)(int, const void *, size_t, int) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const void *, size_t, int))REAL(send);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, buf, len, flags) : -1;
	xlog_ts(&ts, "send(%d,%p,%zu,flags=0x%x) = %zd", fd, buf, len, flags, r);
	return r;
}

ssize_t sendto(int fd, const void *buf, size_t len, int flags,
	       const struct sockaddr *addr, socklen_t alen)
{
	static ssize_t (*real)(int, const void *, size_t, int, const struct sockaddr *, socklen_t) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const void *, size_t, int, const struct sockaddr *, socklen_t))REAL(sendto);
	struct timespec ts;
	char sp[256];
	sock_path(addr, alen, sp, sizeof(sp));
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ssize_t r = real ? real(fd, buf, len, flags, addr, alen) : -1;
	xlog_ts(&ts, "sendto(%d,%p,%zu,flags=0x%x,%s) = %zd", fd, buf, len, flags, sp, r);
	return r;
}

ssize_t sendmsg(int fd, const struct msghdr *msg, int flags)
{
	static ssize_t (*real)(int, const struct msghdr *, int) = NULL;
	if (!real)
		real = (ssize_t(*)(int, const struct msghdr *, int))REAL(sendmsg);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, msg, flags) : -1;
	xlog_ts(&ts, "sendmsg(%d,%p,flags=0x%x) = %zd", fd, (void *)msg, flags, r);
	return r;
}

ssize_t recv(int fd, void *buf, size_t len, int flags)
{
	static ssize_t (*real)(int, void *, size_t, int) = NULL;
	if (!real)
		real = (ssize_t(*)(int, void *, size_t, int))REAL(recv);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, buf, len, flags) : -1;
	xlog_ts(&ts, "recv(%d,%p,%zu,flags=0x%x) = %zd", fd, buf, len, flags, r);
	return r;
}

ssize_t recvfrom(int fd, void *buf, size_t len, int flags,
		 struct sockaddr *addr, socklen_t *alen)
{
	static ssize_t (*real)(int, void *, size_t, int, struct sockaddr *, socklen_t *) = NULL;
	if (!real)
		real = (ssize_t(*)(int, void *, size_t, int, struct sockaddr *, socklen_t *))REAL(recvfrom);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, buf, len, flags, addr, alen) : -1;
	xlog_ts(&ts, "recvfrom(%d,%p,%zu,flags=0x%x) = %zd", fd, buf, len, flags, r);
	return r;
}

ssize_t recvmsg(int fd, struct msghdr *msg, int flags)
{
	static ssize_t (*real)(int, struct msghdr *, int) = NULL;
	if (!real)
		real = (ssize_t(*)(int, struct msghdr *, int))REAL(recvmsg);
	TS_VAR(ts);
	ssize_t r = real ? real(fd, msg, flags) : -1;
	xlog_ts(&ts, "recvmsg(%d,%p,flags=0x%x) = %zd", fd, (void *)msg, flags, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */

int poll(struct pollfd *fds, nfds_t nfds, int timeout)
{
	static int (*real)(struct pollfd *, nfds_t, int) = NULL;
	if (!real)
		real = (int(*)(struct pollfd *, nfds_t, int))REAL(poll);
	TS_VAR(ts);
	int r = real ? real(fds, nfds, timeout) : -1;
	if (fds && nfds <= 8) {
		static char b[256];
		int n = 0, k;
		for (k = 0; k < (int)nfds; k++) {
			n += snprintf(b + n, sizeof(b) - n, " [%d ev=0x%x re=0x%x]",
				      fds[k].fd, (unsigned)fds[k].events,
				      (unsigned)fds[k].revents);
		}
		xlog_ts(&ts, "poll(fds=%p,nfds=%u,timeout=%d) = %d%s",
			(void *)fds, (unsigned)nfds, timeout, r, b);
	} else {
		xlog_ts(&ts, "poll(fds=%p,nfds=%u,timeout=%d) = %d", (void *)fds,
			(unsigned)nfds, timeout, r);
	}
	return r;
}

int ppoll(struct pollfd *fds, nfds_t nfds, const struct timespec *to, const sigset_t *sigmask)
{
	static int (*real)(struct pollfd *, nfds_t, const struct timespec *, const sigset_t *) = NULL;
	if (!real)
		real = (int(*)(struct pollfd *, nfds_t, const struct timespec *, const sigset_t *))REAL(ppoll);
	TS_VAR(ts);
	int r = real ? real(fds, nfds, to, sigmask) : -1;
	xlog_ts(&ts, "ppoll(fds=%p,nfds=%u,to=%p,sigmask=%p) = %d", (void *)fds, (unsigned)nfds,
		(const void *)to, (const void *)sigmask, r);
	return r;
}

int select(int nfds, fd_set *r, fd_set *w, fd_set *e, struct timeval *to)
{
	static int (*real)(int, fd_set *, fd_set *, fd_set *, struct timeval *) = NULL;
	if (!real)
		real = (int(*)(int, fd_set *, fd_set *, fd_set *, struct timeval *))REAL(select);
	TS_VAR(ts);
	int res = real ? real(nfds, r, w, e, to) : -1;
	if (r && nfds > 0 && nfds <= 64) {
		char b[128];
		int n = 0, fd;
		for (fd = 0; fd < nfds && n < (int)sizeof(b) - 4; fd++)
			if (FD_ISSET(fd, r))
				n += snprintf(b + n, sizeof(b) - n, "%d,", fd);
		if (n > 0)
			b[n - 1] = 0;
		else
			b[0] = 0;
		xlog_ts(&ts, "select(nfds=%d,%p,%p,%p,to=%p) = %d ready=[%s]", nfds,
			(void *)r, (void *)w, (void *)e, (void *)to, res, b);
	} else {
		xlog_ts(&ts, "select(nfds=%d,%p,%p,%p,to=%p) = %d", nfds, (void *)r,
			(void *)w, (void *)e, (void *)to, res);
	}
	return res;
}

int pselect(int nfds, fd_set *r, fd_set *w, fd_set *e, const struct timespec *to, const sigset_t *sig)
{
	static int (*real)(int, fd_set *, fd_set *, fd_set *, const struct timespec *, const sigset_t *) = NULL;
	if (!real)
		real = (int(*)(int, fd_set *, fd_set *, fd_set *, const struct timespec *, const sigset_t *))REAL(pselect);
	TS_VAR(ts);
	int res = real ? real(nfds, r, w, e, to, sig) : -1;
	xlog_ts(&ts, "pselect(nfds=%d,%p,%p,%p,to=%p) = %d", nfds, (void *)r, (void *)w, (void *)e, (void *)to, res);
	return res;
}

int epoll_create1(int flags)
{
	static int (*real)(int) = NULL;
	if (!real)
		real = (int(*)(int))REAL(epoll_create1);
	TS_VAR(ts);
	int r = real ? real(flags) : -1;
	xlog_ts(&ts, "epoll_create1(flags=0x%x) = %d", flags, r);
	return r;
}

int epoll_ctl(int epfd, int op, int fd, struct epoll_event *ev)
{
	static int (*real)(int, int, int, struct epoll_event *) = NULL;
	if (!real)
		real = (int(*)(int, int, int, struct epoll_event *))REAL(epoll_ctl);
	TS_VAR(ts);
	int r = real ? real(epfd, op, fd, ev) : -1;
	xlog_ts(&ts, "epoll_ctl(%d,op=%d,fd=%d,%p) = %d", epfd, op, fd, (void *)ev, r);
	return r;
}

int epoll_wait(int epfd, struct epoll_event *ev, int max, int to)
{
	static int (*real)(int, struct epoll_event *, int, int) = NULL;
	if (!real)
		real = (int(*)(int, struct epoll_event *, int, int))REAL(epoll_wait);
	TS_VAR(ts);
	int r = real ? real(epfd, ev, max, to) : -1;
	xlog_ts(&ts, "epoll_wait(%d,%p,%d,to=%d) = %d", epfd, (void *)ev, max, to, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* time                                                                */
/* ------------------------------------------------------------------ */

int clock_gettime(clockid_t clk, struct timespec *ts_out)
{
	static int (*real)(clockid_t, struct timespec *) = NULL;
	if (!real)
		real = (int(*)(clockid_t, struct timespec *))REAL(clock_gettime);
	TS_VAR(ts);
	int r = real ? real(clk, ts_out) : -1;
	xlog_ts(&ts, "clock_gettime(clk=%d) = %d", (int)clk, r);
	return r;
}

int gettimeofday(struct timeval *tv, void *tz)
{
	static int (*real)(struct timeval *, void *) = NULL;
	if (!real)
		real = (int(*)(struct timeval *, void *))REAL(gettimeofday);
	TS_VAR(ts);
	int r = real ? real(tv, tz) : -1;
	xlog_ts(&ts, "gettimeofday(%p,%p) = %d", (void *)tv, tz, r);
	return r;
}

time_t time(time_t *t)
{
	static time_t (*real)(time_t *) = NULL;
	if (!real)
		real = (time_t(*)(time_t *))REAL(time);
	TS_VAR(ts);
	time_t r = real ? real(t) : (time_t)-1;
	xlog_ts(&ts, "time(%p) = %ld", (void *)t, (long)r);
	return r;
}

int nanosleep(const struct timespec *req, struct timespec *rem)
{
	static int (*real)(const struct timespec *, struct timespec *) = NULL;
	if (!real)
		real = (int(*)(const struct timespec *, struct timespec *))REAL(nanosleep);
	TS_VAR(ts);
	int r = real ? real(req, rem) : -1;
	xlog_ts(&ts, "nanosleep(req={%ld,%ld}) = %d", req ? (long)req->tv_sec : -1,
		req ? req->tv_nsec : -1, r);
	return r;
}

int clock_nanosleep(clockid_t clk, int flags, const struct timespec *req, struct timespec *rem)
{
	static int (*real)(clockid_t, int, const struct timespec *, struct timespec *) = NULL;
	if (!real)
		real = (int(*)(clockid_t, int, const struct timespec *, struct timespec *))REAL(clock_nanosleep);
	TS_VAR(ts);
	int r = real ? real(clk, flags, req, rem) : -1;
	xlog_ts(&ts, "clock_nanosleep(clk=%d,flags=0x%x) = %d", (int)clk, flags, r);
	return r;
}

int usleep(useconds_t usec)
{
	static int (*real)(useconds_t) = NULL;
	if (!real)
		real = (int(*)(useconds_t))REAL(usleep);
	TS_VAR(ts);
	int r = real ? real(usec) : -1;
	xlog_ts(&ts, "usleep(%u) = %d", (unsigned)usec, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* process / signal                                                    */
/* ------------------------------------------------------------------ */

int uname(struct utsname *buf)
{
	static int (*real)(struct utsname *) = NULL;
	if (!real)
		real = (int(*)(struct utsname *))REAL(uname);
	TS_VAR(ts);
	int r = real ? real(buf) : -1;
	xlog_ts(&ts, "uname(%p) = %d", (void *)buf, r);
	return r;
}

pid_t fork(void)
{
	static pid_t (*real)(void) = NULL;
	if (!real)
		real = (pid_t(*)(void))REAL(fork);
	TS_VAR(ts);
	pid_t r = real ? real() : -1;
	xlog_ts(&ts, "fork() = %d", (int)r);
	return r;
}

pid_t vfork(void)
{
	static pid_t (*real)(void) = NULL;
	if (!real)
		real = (pid_t(*)(void))REAL(vfork);
	TS_VAR(ts);
	pid_t r = real ? real() : -1;
	xlog_ts(&ts, "vfork() = %d", (int)r);
	return r;
}

int execve(const char *path, char *const argv[], char *const envp[])
{
	static int (*real)(const char *, char *const[], char *const[]) = NULL;
	if (!real)
		real = (int(*)(const char *, char *const[], char *const[]))REAL(execve);
	TS_VAR(ts);
	int r = real ? real(path, argv, envp) : -1;
	xlog_ts(&ts, "execve(%s,argv=%p) = %d", qstr(path), (void *)argv, r);
	return r;
}

int execvp(const char *path, char *const argv[])
{
	static int (*real)(const char *, char *const[]) = NULL;
	if (!real)
		real = (int(*)(const char *, char *const[]))REAL(execvp);
	TS_VAR(ts);
	int r = real ? real(path, argv) : -1;
	xlog_ts(&ts, "execvp(%s,argv=%p) = %d", qstr(path), (void *)argv, r);
	return r;
}

pid_t waitpid(pid_t pid, int *status, int options)
{
	static pid_t (*real)(pid_t, int *, int) = NULL;
	if (!real)
		real = (pid_t(*)(pid_t, int *, int))REAL(waitpid);
	TS_VAR(ts);
	pid_t r = real ? real(pid, status, options) : -1;
	xlog_ts(&ts, "waitpid(%d,%p,opts=0x%x) = %d", (int)pid, (void *)status, options, (int)r);
	return r;
}

pid_t wait4(pid_t pid, int *status, int options, struct rusage *ru)
{
	static pid_t (*real)(pid_t, int *, int, struct rusage *) = NULL;
	if (!real)
		real = (pid_t(*)(pid_t, int *, int, struct rusage *))REAL(wait4);
	TS_VAR(ts);
	pid_t r = real ? real(pid, status, options, ru) : -1;
	xlog_ts(&ts, "wait4(%d,%p,opts=0x%x) = %d", (int)pid, (void *)status, options, (int)r);
	return r;
}

void exit(int code)
{
	static void (*real)(int) = NULL;
	if (!real)
		real = (void(*)(int))REAL(exit);
	TS_VAR(ts);
	xlog_ts(&ts, "exit(%d)", code);
	if (real)
		real(code);
}

void _exit(int code)
{
	static void (*real)(int) = NULL;
	if (!real)
		real = (void(*)(int))REAL(_exit);
	TS_VAR(ts);
	xlog_ts(&ts, "_exit(%d)", code);
	if (real)
		real(code);
}

void _Exit(int code)
{
	static void (*real)(int) = NULL;
	if (!real)
		real = (void(*)(int))REAL(_Exit);
	TS_VAR(ts);
	xlog_ts(&ts, "_Exit(%d)", code);
	if (real)
		real(code);
}

unsigned int sleep(unsigned int seconds)
{
	static unsigned int (*real)(unsigned int) = NULL;
	if (!real)
		real = (unsigned int(*)(unsigned int))REAL(sleep);
	TS_VAR(ts);
	unsigned int r = real ? real(seconds) : 0;
	xlog_ts(&ts, "sleep(%u) = %u", seconds, r);
	return r;
}

int sched_yield(void)
{
	static int (*real)(void) = NULL;
	if (!real)
		real = (int(*)(void))REAL(sched_yield);
	TS_VAR(ts);
	int r = real ? real() : -1;
	xlog_ts(&ts, "sched_yield() = %d", r);
	return r;
}

int getrlimit(int resource, struct rlimit *rlim)
{
	static int (*real)(int, struct rlimit *) = NULL;
	if (!real)
		real = (int(*)(int, struct rlimit *))REAL(getrlimit);
	TS_VAR(ts);
	int r = real ? real(resource, rlim) : -1;
	xlog_ts(&ts, "getrlimit(%d,%p) = %d", resource, (void *)rlim, r);
	return r;
}

int setrlimit(int resource, const struct rlimit *rlim)
{
	static int (*real)(int, const struct rlimit *) = NULL;
	if (!real)
		real = (int(*)(int, const struct rlimit *))REAL(setrlimit);
	TS_VAR(ts);
	int r = real ? real(resource, rlim) : -1;
	xlog_ts(&ts, "setrlimit(%d,%p) = %d", resource, (const void *)rlim, r);
	return r;
}

int prlimit64(pid_t pid, int resource, const struct rlimit *nl, struct rlimit *ol)
{
	static int (*real)(pid_t, int, const struct rlimit *, struct rlimit *) = NULL;
	if (!real)
		real = (int(*)(pid_t, int, const struct rlimit *, struct rlimit *))REAL(prlimit64);
	TS_VAR(ts);
	int r = real ? real(pid, resource, nl, ol) : -1;
	xlog_ts(&ts, "prlimit64(%d,%d,%p,%p) = %d", (int)pid, resource, (const void *)nl, (void *)ol, r);
	return r;
}

int getrlimit64(int resource, struct rlimit64 *rlim)
{
	static int (*real)(int, struct rlimit64 *) = NULL;
	if (!real)
		real = (int(*)(int, struct rlimit64 *))REAL(getrlimit64);
	TS_VAR(ts);
	int r = real ? real(resource, rlim) : -1;
	xlog_ts(&ts, "getrlimit64(%d,%p) = %d", resource, (void *)rlim, r);
	return r;
}

int sysinfo(struct sysinfo *info)
{
	static int (*real)(struct sysinfo *) = NULL;
	if (!real)
		real = (int(*)(struct sysinfo *))REAL(sysinfo);
	TS_VAR(ts);
	int r = real ? real(info) : -1;
	xlog_ts(&ts, "sysinfo(%p) = %d", (void *)info, r);
	return r;
}

int sigaction(int sig, const struct sigaction *act, struct sigaction *old)
{
	static int (*real)(int, const struct sigaction *, struct sigaction *) = NULL;
	if (!real)
		real = (int(*)(int, const struct sigaction *, struct sigaction *))REAL(sigaction);
	TS_VAR(ts);
	int r = real ? real(sig, act, old) : -1;
	xlog_ts(&ts, "sigaction(%d,%p,%p) = %d", sig, (const void *)act, (void *)old, r);
	return r;
}

int sigprocmask(int how, const sigset_t *set, sigset_t *old)
{
	static int (*real)(int, const sigset_t *, sigset_t *) = NULL;
	if (!real)
		real = (int(*)(int, const sigset_t *, sigset_t *))REAL(sigprocmask);
	TS_VAR(ts);
	int r = real ? real(how, set, old) : -1;
	xlog_ts(&ts, "sigprocmask(%d,%p,%p) = %d", how, (const void *)set, (void *)old, r);
	return r;
}

int kill(pid_t pid, int sig)
{
	static int (*real)(pid_t, int) = NULL;
	if (!real)
		real = (int(*)(pid_t, int))REAL(kill);
	TS_VAR(ts);
	int r = real ? real(pid, sig) : -1;
	xlog_ts(&ts, "kill(%d,sig=%d) = %d", (int)pid, sig, r);
	return r;
}

int getrandom(void *buf, size_t buflen, unsigned int flags)
{
	static int (*real)(void *, size_t, unsigned int) = NULL;
	if (!real)
		real = (int(*)(void *, size_t, unsigned int))REAL(getrandom);
	TS_VAR(ts);
	int r = real ? real(buf, buflen, flags) : -1;
	xlog_ts(&ts, "getrandom(%p,%zu,flags=0x%x) = %d", buf, buflen, flags, r);
	return r;
}

int mprotect(void *addr, size_t len, int prot)
{
	static int (*real)(void *, size_t, int) = NULL;
	if (!real)
		real = (int(*)(void *, size_t, int))REAL(mprotect);
	TS_VAR(ts);
	int r = real ? real(addr, len, prot) : -1;
	xlog_ts(&ts, "mprotect(%p,%zu,prot=0x%x) = %d", addr, len, prot, r);
	return r;
}
