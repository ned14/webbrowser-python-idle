/*
 * xsync-fix.so - short-circuit Xlib's sync/flush round-trips for Tk.
 *
 * The Tk-under-CheerpX busy loop (§2.3/§2.5) is `window.update()` calling
 * `Tcl_DoOneEvent(TCL_DONT_WAIT)` in a loop; each iteration Tk's X event
 * source issues `XNoOp` + `XEventsQueued(QueuedAfterFlush)` + `XFlush`
 * (XSync-style). The guest X server never completes the NoOp round-trip, so
 * `XEventsQueued` returns 0 forever and update() never drains.
 *
 * Fix: make the sync primitives complete immediately. XNoOp's request is a
 * pure barrier - skipping it leaves no reply pending, so XEventsQueued sees
 * an empty queue and update() terminates. XSync is made a no-op for the same
 * reason. XEventsQueued is left intact (Tk still needs the local queue
 * count), but with no XNoOp outstanding nothing blocks.
 *
 * Scope: ONLY these three Xlib entry points. Everything else (poll/select/
 * recvmsg/XNextEvent) is untouched - real X events still flow to mainloop.
 *
 * Symbols are resolved from libX11 via dlopen (libX11 is NOT in the
 * preloaded library's dependency chain), same pattern as xcall-logger.c.
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -shared -fPIC -o xsync-fix.so xsync-fix.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>

typedef struct _XDisplay Display;

int XNoOp(Display *dpy)
{
	static int (*real)(Display *) = NULL;
	if (!real) {
		void *h = dlopen("libX11.so.6", RTLD_LAZY | RTLD_GLOBAL);
		if (h)
			real = (int (*)(Display *))dlsym(h, "XNoOp");
	}
	/* Skip the round-trip entirely: no request queued, no reply pending. */
	return 1;
}

int XSync(Display *dpy, int discard)
{
	static int (*real)(Display *, int) = NULL;
	if (!real) {
		void *h = dlopen("libX11.so.6", RTLD_LAZY | RTLD_GLOBAL);
		if (h)
			real = (int (*)(Display *, int))dlsym(h, "XSync");
	}
	/* No-op: do not send GetInputFocus/NoOp, do not wait for a reply. */
	return 1;
}

int XEventsQueued(Display *dpy, int mode)
{
	static int (*real)(Display *, int) = NULL;
	if (!real) {
		void *h = dlopen("libX11.so.6", RTLD_LAZY | RTLD_GLOBAL);
		if (h)
			real = (int (*)(Display *, int))dlsym(h, "XEventsQueued");
	}
	if (real)
		return real(dpy, mode);
	return 0;
}
