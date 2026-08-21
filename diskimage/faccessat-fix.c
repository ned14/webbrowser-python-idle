/*
 * faccessat-fix.c — LD_PRELOAD shim for the CheerpX guest (Tier B).
 *
 * CheerpX's faccessat implementation traps the whole emulator (wasm crash,
 * wild read; the page reports "Fault ... proc /sbin/openrc" and the trap
 * report) when the dirfd is -1. OpenRC 0.60+ hits faccessat(-1, ...) BY
 * DESIGN: the service state table lists RC_SERVICE_STOPPED and
 * RC_SERVICE_CRASHED with RC_DIR_INVALID, rc_dirfd() returns -1 for
 * RC_DIR_INVALID, and rc_service_state()/rc_service_mark() then call
 * faccessat(-1, <svc>, F_OK, 0) for every service on every runlevel change.
 * On the kernel that is a plain EBADF; under CheerpX it crashes the guest.
 *
 * (The previous Alpine 3.17 guest used openrc 0.5x, whose state checks were
 * path-based exists() calls — no faccessat — which is why this only broke
 * with the 3.24 upgrade. Diagnosed 2026-08-20 via an LD_PRELOAD syscall
 * trace in the CheerpX guest; see plans/update-to-latest.md Tier B §9.2.)
 *
 * The shim short-circuits the *at() family for dfd < 0 (except AT_FDCWD,
 * which is a valid relative-to-cwd marker) to errno=EBADF — exactly what
 * the kernel does — and passes every other call through to libc. Loading
 * it for the whole boot chain (inittab sysinit/boot/default lines) also
 * protects every guest process from the same CheerpX defect.
 *
 * Second CheerpX defect fixed here (diagnosed 2026-08-20): openrc's
 * exec_service() forks a child, restores SIG_DFL handlers and then calls
 * sigprocmask(SIG_UNBLOCK, &full_mask, NULL) before exec'ing the init
 * script. Under CheerpX that sigprocmask call crashes the child with a
 * wild call (wasm "function signature mismatch"; Fault addr==ip==0xffff9fa7,
 * proc <init script>) — likely a pending-signal delivery through corrupted
 * emulated handler state after fork. The implementation below converts
 * SIG_UNBLOCK into the WORKING SIG_SETMASK branch (read the current mask
 * with SETMASK(NULL), clear the requested bits, write it back) — a naive
 * "SIG_UNBLOCK → no-op" left ALL signals blocked in the child and the
 * exec'd init scripts misbehaved; the faithful conversion is what works.
 * No other boot path uses SIG_UNBLOCK (openrc restores masks with
 * SIG_SETMASK, which works), and the child's mask is reset by the upcoming
 * execve anyway.
 *
 * Build (i386 musl): gcc -O2 -shared -fPIC -o faccessat-fix.so faccessat-fix.c
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stddef.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef AT_FDCWD
#define AT_FDCWD -100
#endif

/* Bad dirfd: a negative fd that is not the relative-to-cwd marker. */
static int bad_dfd(int dfd)
{
	return dfd < 0 && dfd != AT_FDCWD;
}

typedef int (*faccessat_fn)(int, const char *, int, int);
typedef int (*unlinkat_fn)(int, const char *, int);
typedef int (*fstatat_fn)(int, const char *, struct stat *, int);
typedef int (*mkdirat_fn)(int, const char *, mode_t);
typedef int (*openat_fn)(int, const char *, int, ...);
typedef int (*renameat_fn)(int, const char *, int, const char *);
typedef int (*symlinkat_fn)(const char *, int, const char *);
typedef ssize_t (*readlinkat_fn)(int, const char *, char *, size_t);
typedef int (*utimensat_fn)(int, const char *, const struct timespec[2], int);
typedef int (*sigprocmask_fn)(int, const sigset_t *, sigset_t *);

#include <poll.h>
#include <sys/types.h>

static faccessat_fn real_faccessat;
static unlinkat_fn real_unlinkat;
static fstatat_fn real_fstatat;
static mkdirat_fn real_mkdirat;
static openat_fn real_openat;
static renameat_fn real_renameat;
static symlinkat_fn real_symlinkat;
static readlinkat_fn real_readlinkat;
static utimensat_fn real_utimensat;
static sigprocmask_fn real_sigprocmask;

int
faccessat(int dfd, const char *path, int amode, int flags)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_faccessat)
		real_faccessat = (faccessat_fn)dlsym(RTLD_NEXT, "faccessat");
	return real_faccessat(dfd, path, amode, flags);
}

int
unlinkat(int dfd, const char *path, int flags)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_unlinkat)
		real_unlinkat = (unlinkat_fn)dlsym(RTLD_NEXT, "unlinkat");
	return real_unlinkat(dfd, path, flags);
}

int
fstatat(int dfd, const char *path, struct stat *st, int flags)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_fstatat)
		real_fstatat = (fstatat_fn)dlsym(RTLD_NEXT, "fstatat");
	return real_fstatat(dfd, path, st, flags);
}

int
mkdirat(int dfd, const char *path, mode_t mode)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_mkdirat)
		real_mkdirat = (mkdirat_fn)dlsym(RTLD_NEXT, "mkdirat");
	return real_mkdirat(dfd, path, mode);
}

int
openat(int dfd, const char *path, int flags, ...)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_openat)
		real_openat = (openat_fn)dlsym(RTLD_NEXT, "openat");
	{
		va_list ap;
		mode_t mode;
		va_start(ap, flags);
		mode = (mode_t)va_arg(ap, int);
		va_end(ap);
		return real_openat(dfd, path, flags, mode);
	}
}

int
renameat(int olddfd, const char *oldpath, int newdfd, const char *newpath)
{
	if (bad_dfd(olddfd) || bad_dfd(newdfd)) { errno = EBADF; return -1; }
	if (!real_renameat)
		real_renameat = (renameat_fn)dlsym(RTLD_NEXT, "renameat");
	return real_renameat(olddfd, oldpath, newdfd, newpath);
}

int
symlinkat(const char *target, int dfd, const char *linkpath)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_symlinkat)
		real_symlinkat = (symlinkat_fn)dlsym(RTLD_NEXT, "symlinkat");
	return real_symlinkat(target, dfd, linkpath);
}

ssize_t
readlinkat(int dfd, const char *path, char *buf, size_t bufsiz)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_readlinkat)
		real_readlinkat = (readlinkat_fn)dlsym(RTLD_NEXT, "readlinkat");
	return real_readlinkat(dfd, path, buf, bufsiz);
}

int
utimensat(int dfd, const char *path, const struct timespec times[2], int flags)
{
	if (bad_dfd(dfd)) { errno = EBADF; return -1; }
	if (!real_utimensat)
		real_utimensat = (utimensat_fn)dlsym(RTLD_NEXT, "utimensat");
	return real_utimensat(dfd, path, times, flags);
}

#ifndef SIG_UNBLOCK
#define SIG_UNBLOCK 1
#endif
#ifndef SIG_SETMASK
#define SIG_SETMASK 2
#endif

#ifndef POLLIN
#define POLLIN 0x001
#endif

#ifndef SOL_SOCKET
#define SOL_SOCKET 1
#endif
#ifndef SO_PASSCRED
#define SO_PASSCRED 16
#endif

typedef int (*setsockopt_fn)(int, int, int, const void *, unsigned int);

static setsockopt_fn real_setsockopt;

int
setsockopt(int fd, int level, int optname, const void *optval,
	   unsigned int optlen)
{
	/* CheerpX defect (diagnosed 2026-08-20): its netlink emulation does
	 * not implement setsockopt(SOL_SOCKET, SO_PASSCRED) — it returns
	 * EPROTONOSUPPORT and logs an endless "TODO: SYS_SETSOCKOPT" retry
	 * loop (udevd's netlink setup busy-spins and wedges the whole
	 * emulator). udevd needs the call to SUCCEED; nothing in the guest
	 * depends on actual credential passing (the emulated netlink has no
	 * senders with credentials anyway). */
	if (level == SOL_SOCKET && optname == SO_PASSCRED) {
		(void)fd; (void)optval; (void)optlen;
		return 0;
	}
	if (!real_setsockopt)
		real_setsockopt = (setsockopt_fn)dlsym(RTLD_NEXT, "setsockopt");
	return real_setsockopt(fd, level, optname, optval, optlen);
}

struct pollfd;
typedef int (*poll_fn)(struct pollfd *, unsigned long, int);
typedef int (*ppoll_fn)(struct pollfd *, unsigned long,
			const struct timespec *, const sigset_t *);

static poll_fn real_poll;
static ppoll_fn real_ppoll;

int
ppoll(struct pollfd *fds, unsigned long nfds,
      const struct timespec *tmo, const sigset_t *sigmask)
{
	/* CheerpX defect (diagnosed 2026-08-20): the ppoll syscall returns
	 * -1 with errno=0 (never waits, never reports readiness). GLib's
	 * g_poll uses ppoll, so openbox/dbus main loops fail every
	 * iteration ("poll(2) failed due to: Function not implemented"
	 * flood) and windows never map. poll() works — convert ppoll to
	 * poll: ms timeout from the timespec (negative = infinite, 0 =
	 * poll once), and the sigmask is deliberately ignored (the
	 * emulated guest has no signal-driven wakeup use that requires
	 * it here; the mask is reset by any exec anyway). */
	int ms;
	if (!real_poll)
		real_poll = (poll_fn)dlsym(RTLD_NEXT, "poll");
	if (tmo) {
		long long msll = (long long)tmo->tv_sec * 1000 +
				 tmo->tv_nsec / 1000000;
		if (msll < 0)
			msll = 0;
		if (msll > 2147483647LL)
			msll = 2147483647LL;
		ms = (int)msll;
	} else {
		ms = -1;
	}
	(void)sigmask;
	return real_poll(fds, nfds, ms);
}

int
sigprocmask(int how, const sigset_t *set, sigset_t *old)
{
	/* CheerpX crash workaround: the SIG_UNBLOCK branch of its
	 * rt_sigprocmask emulation traps the whole emulator (wild call;
	 * "function signature mismatch"; Fault addr==ip==0xffff9fa7) — it
	 * has to read the thread's current mask and clear bits, and that
	 * read path is broken (observed in openrc's exec_service child
	 * before exec). The SIG_SETMASK branch (plain overwrite) works.
	 * Implement SIG_UNBLOCK faithfully via the working branch: read the
	 * current mask with SETMASK(NULL), clear the requested bits, and
	 * write it back. (Diagnosed 2026-08-20; see the file header + 
	 * plans/update-to-latest.md Tier B §9.2.) */
	if (how == SIG_UNBLOCK) {
		sigset_t cur, clear;
		if (!real_sigprocmask)
			real_sigprocmask = (sigprocmask_fn)dlsym(RTLD_NEXT, "sigprocmask");
		if (real_sigprocmask(SIG_SETMASK, NULL, &cur) != 0)
			return -1;
		if (set) {
			clear = cur;
			for (int s = 1; s < NSIG; s++)
				if (sigismember(set, s))
					sigdelset(&clear, s);
			cur = clear;
		}
		return real_sigprocmask(SIG_SETMASK, &cur, old);
	}
	if (!real_sigprocmask)
		real_sigprocmask = (sigprocmask_fn)dlsym(RTLD_NEXT, "sigprocmask");
	return real_sigprocmask(how, set, old);
}
