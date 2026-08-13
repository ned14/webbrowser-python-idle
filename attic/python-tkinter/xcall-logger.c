/*
 * xcall-logger.c - LD_PRELOAD shim that logs X11 entry points (Xlib core,
 * plus libXft if the client loads it) to stderr, one line per call.
 *
 * Each line:  X11<TAB><monotonic_sec.usec><TAB><func>(<args>) -> <ret>
 * The timestamp is captured at function ENTRY so the ordering matches the
 * syscall trace captured alongside (strace -tt is wall-clock; this is
 * CLOCK_MONOTONIC - correlate via the write(2) of the marker line, both
 * traces are cut at TRACE_MAINLOOP_BEGIN).
 *
 * Build (i386 musl, Alpine 3.17 - the same userland the CheerpX guest runs):
 *   gcc -O2 -shared -fPIC -o xcall-logger.so xcall-logger.c -ldl
 *
 * Resolution of the real symbols uses dlopen(3)+dlsym(3), NEVER RTLD_NEXT:
 * inside the Tk process RTLD_NEXT returned NULL for libX11 symbols (see
 * plans/display-bug.md, "Tk hang deep-dive").
 *
 * Only PUBLIC Xlib calls made by the application are logged (calls resolved
 * through the dynamic symbol table).  Calls made internally between libX11's
 * own functions are not interposed - that is intentional: the trace is the
 * canonical record of the calls the Tk application itself makes.
 *
 * Xlib MACROS (XBlackPixel, XWhitePixel, XDefaultScreen, XDefaultRootWindow,
 * XRootWindow, XDisplayWidth/Height, XVisualIDFromVisual, ...) are not real
 * exported functions and cannot be interposed; they resolve to structure
 * field reads and therefore produce NO call in the trace.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <dlfcn.h>
#include <time.h>

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/XKBlib.h>
#include <X11/Xresource.h>
#include <X11/keysym.h>

/* Xlib headers define these as macros; the underlying library still exports
 * the real functions and Tk references the FUNCTION symbols, so interpose the
 * functions and neutralise the macro forms. */
#ifdef XRootWindow
#undef XRootWindow
#endif
#ifdef XKeycodeToKeysym
#undef XKeycodeToKeysym
#endif
#ifdef XLookupKeysym
#undef XLookupKeysym
#endif
#ifdef XVisualIDFromVisual
#undef XVisualIDFromVisual
#endif

/* ------------------------------------------------------------------ */
/* logging helpers                                                     */
/* ------------------------------------------------------------------ */

static void xlog_ts(const struct timespec *ts, const char *fmt, ...)
{
	char buf[4096];
	va_list ap;
	int n = snprintf(buf, sizeof(buf), "X11\t%ld.%06ld\t",
			 (long)ts->tv_sec, (long)ts->tv_nsec / 1000);
	if (n < 0 || n >= (int)sizeof(buf))
		return;
	va_start(ap, fmt);
	vsnprintf(buf + n, sizeof(buf) - n, fmt, ap);
	va_end(ap);
	/* single write() per line so two concurrent logger processes cannot
	 * interleave mid-line on the shared console */
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
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Entry logging (diagnostic): when XCALL_LOG_ENTRY is defined, every
 * interposed function also logs a line at ENTRY (before the real call),
 * using __func__ for the name. A function that is entered but never returns
 * (e.g. the CheerpX Tk hang) then shows up as the LAST line of the trace,
 * which the return-only loggers cannot see. The default build is unchanged
 * (entry lines compiled out). */
#ifdef XCALL_LOG_ENTRY
#define ENTRY_LOG(ts) xlog_ts((ts), "%s ENTERED", __func__)
#else
#define ENTRY_LOG(ts) ((void)0)
#endif

/* Quote a (possibly binary / long) string, truncating to 100 chars. */
static const char *qstr(const char *s, int len)
{
	static char b[160];
	int n = (len < 0) ? (s ? (int)strlen(s) : 0) : len;
	if (n > 100)
		n = 100;
	snprintf(b, sizeof(b), "\"%.*s%s\"", n, s ? s : "(null)",
		 len < 0 ? "" : (len > 100 ? "..." : ""));
	return b;
}

/* ------------------------------------------------------------------ */
/* real-symbol resolution (dlopen+dlsym, per-library)                  */
/* ------------------------------------------------------------------ */

static void *real_from(const char *lib, const char *name)
{
	static void *h_x11 = NULL, *h_xft = NULL;
	void *h = NULL;

	if (strcmp(lib, "libX11.so.6") == 0) {
		if (!h_x11) {
			h_x11 = dlopen("libX11.so.6", RTLD_LAZY | RTLD_GLOBAL);
			if (!h_x11)
				fprintf(stderr, "X11\t%.6f\tlogger: dlopen(libX11.so.6): %s\n",
					now_sec(), dlerror());
		}
		h = h_x11;
	} else if (strcmp(lib, "libXft.so.2") == 0) {
		if (!h_xft) {
			h_xft = dlopen("libXft.so.2", RTLD_LAZY | RTLD_GLOBAL);
			if (!h_xft)
				fprintf(stderr, "X11\t%.6f\tlogger: dlopen(libXft.so.2): %s\n",
					now_sec(), dlerror());
		}
		h = h_xft;
	}
	if (!h)
		return NULL;
	void *p = dlsym(h, name);
	if (!p)
		fprintf(stderr, "X11\t%.6f\tlogger: dlsym(%s): %s\n",
			now_sec(), name, dlerror());
	return p;
}

#define X11_FN(name) \
	(real_from("libX11.so.6", name))

static const char *event_name(int t)
{
	switch (t) {
	case KeyPress: return "KeyPress";
	case KeyRelease: return "KeyRelease";
	case ButtonPress: return "ButtonPress";
	case ButtonRelease: return "ButtonRelease";
	case MotionNotify: return "MotionNotify";
	case EnterNotify: return "EnterNotify";
	case LeaveNotify: return "LeaveNotify";
	case FocusIn: return "FocusIn";
	case FocusOut: return "FocusOut";
	case KeymapNotify: return "KeymapNotify";
	case Expose: return "Expose";
	case GraphicsExpose: return "GraphicsExpose";
	case NoExpose: return "NoExpose";
	case VisibilityNotify: return "VisibilityNotify";
	case CreateNotify: return "CreateNotify";
	case DestroyNotify: return "DestroyNotify";
	case UnmapNotify: return "UnmapNotify";
	case MapNotify: return "MapNotify";
	case MapRequest: return "MapRequest";
	case ReparentNotify: return "ReparentNotify";
	case ConfigureNotify: return "ConfigureNotify";
	case ConfigureRequest: return "ConfigureRequest";
	case GravityNotify: return "GravityNotify";
	case ResizeRequest: return "ResizeRequest";
	case CirculateNotify: return "CirculateNotify";
	case CirculateRequest: return "CirculateRequest";
	case PropertyNotify: return "PropertyNotify";
	case SelectionClear: return "SelectionClear";
	case SelectionRequest: return "SelectionRequest";
	case SelectionNotify: return "SelectionNotify";
	case ColormapNotify: return "ColormapNotify";
	case ClientMessage: return "ClientMessage";
	case MappingNotify: return "MappingNotify";
	case GenericEvent: return "GenericEvent";
	default: return "?";
	}
}

static const char *atom_name(Display *d, Atom a)
{
	static char b[128];
	static char *(*real_getatomname)(Display *, Atom) = NULL;
	static int (*real_free)(void *) = NULL;

	if (a == 0) {
		snprintf(b, sizeof(b), "None");
		return b;
	}
	if (!real_getatomname)
		real_getatomname = (char *(*)(Display *, Atom))X11_FN("XGetAtomName");
	if (!real_free)
		real_free = (int(*)(void *))X11_FN("XFree");
	const char *nm = real_getatomname ? real_getatomname(d, a) : NULL;
	if (nm) {
		snprintf(b, sizeof(b), "%s(0x%lx)", nm, (unsigned long)a);
		if (real_free)
			real_free((void *)nm);
	} else {
		snprintf(b, sizeof(b), "0x%lx", (unsigned long)a);
	}
	return b;
}

/* ------------------------------------------------------------------ */
/* connection / lifecycle                                              */
/* ------------------------------------------------------------------ */

Display *XOpenDisplay(const char *dpy_name)
{
	static Display *(*real)(const char *) = NULL;
	if (!real)
		real = (Display *(*)(const char *))X11_FN("XOpenDisplay");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Display *r = real ? real(dpy_name) : NULL;
	xlog_ts(&ts, "XOpenDisplay(%s) -> %p", dpy_name ? dpy_name : "(null)", (void *)r);
	return r;
}

int XCloseDisplay(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XCloseDisplay");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XCloseDisplay(d=%p) -> %d", (void *)d, r);
	return r;
}

Status XInitThreads(void)
{
	static Status (*real)(void) = NULL;
	if (!real)
		real = (Status(*)(void))X11_FN("XInitThreads");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real() : 0;
	xlog_ts(&ts, "XInitThreads() -> %d", (int)r);
	return r;
}

Bool XSupportsLocale(void)
{
	static Bool (*real)(void) = NULL;
	if (!real)
		real = (Bool(*)(void))X11_FN("XSupportsLocale");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real() : 0;
	xlog_ts(&ts, "XSupportsLocale() -> %d", (int)r);
	return r;
}

char *XSetLocaleModifiers(const char *mods)
{
	static char *(*real)(const char *) = NULL;
	if (!real)
		real = (char *(*)(const char *))X11_FN("XSetLocaleModifiers");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = real ? real(mods) : NULL;
	xlog_ts(&ts, "XSetLocaleModifiers(%s) -> %s", mods ? qstr(mods, -1) : "(null)",
		r ? qstr(r, -1) : "(null)");
	return r;
}

XErrorHandler XSetErrorHandler(XErrorHandler h)
{
	static XErrorHandler (*real)(XErrorHandler) = NULL;
	if (!real)
		real = (XErrorHandler(*)(XErrorHandler))X11_FN("XSetErrorHandler");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XErrorHandler r = real ? real(h) : NULL;
	xlog_ts(&ts, "XSetErrorHandler(handler=%p) -> %p", (void *)h, (void *)r);
	return r;
}

XIOErrorHandler XSetIOErrorHandler(XIOErrorHandler h)
{
	static XIOErrorHandler (*real)(XIOErrorHandler) = NULL;
	if (!real)
		real = (XIOErrorHandler(*)(XIOErrorHandler))X11_FN("XSetIOErrorHandler");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XIOErrorHandler r = real ? real(h) : NULL;
	xlog_ts(&ts, "XSetIOErrorHandler(handler=%p) -> %p", (void *)h, (void *)r);
	return r;
}

/* ------------------------------------------------------------------ */
/* window creation / configuration                                     */
/* ------------------------------------------------------------------ */

Window XCreateWindow(Display *d, Window parent, int x, int y, unsigned int w,
		     unsigned int h, unsigned int bw, int depth, unsigned int cls,
		     Visual *vis, unsigned long mask, XSetWindowAttributes *attrs)
{
	static Window (*real)(Display *, Window, int, int, unsigned int, unsigned int,
			      unsigned int, int, unsigned int, Visual *, unsigned long,
			      XSetWindowAttributes *) = NULL;
	if (!real)
		real = (Window(*)(Display *, Window, int, int, unsigned int, unsigned int,
				 unsigned int, int, unsigned int, Visual *, unsigned long,
				 XSetWindowAttributes *))X11_FN("XCreateWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Window r = real ? real(d, parent, x, y, w, h, bw, depth, cls, vis, mask, attrs) : 0;
	xlog_ts(&ts, "XCreateWindow(d=%p,parent=0x%lx,x=%d,y=%d,w=%u,h=%u,bw=%u,depth=%d,class=%u,visual=%p,mask=0x%lx,attrs=%p) -> 0x%lx",
		(void *)d, (unsigned long)parent, x, y, w, h, bw, depth, cls, (void *)vis,
		mask, (void *)attrs, (unsigned long)r);
	return r;
}

Window XCreateSimpleWindow(Display *d, Window parent, int x, int y, unsigned int w,
			   unsigned int h, unsigned int bw, unsigned long border,
			   unsigned long background)
{
	static Window (*real)(Display *, Window, int, int, unsigned int, unsigned int,
			      unsigned int, unsigned long, unsigned long) = NULL;
	if (!real)
		real = (Window(*)(Display *, Window, int, int, unsigned int, unsigned int,
				 unsigned int, unsigned long, unsigned long))X11_FN("XCreateSimpleWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Window r = real ? real(d, parent, x, y, w, h, bw, border, background) : 0;
	xlog_ts(&ts, "XCreateSimpleWindow(d=%p,parent=0x%lx,x=%d,y=%d,w=%u,h=%u,bw=%u,border=0x%lx,bg=0x%lx) -> 0x%lx",
		(void *)d, (unsigned long)parent, x, y, w, h, bw, border, background, (unsigned long)r);
	return r;
}

int XDestroyWindow(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XDestroyWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XDestroyWindow(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XMapWindow(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XMapWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XMapWindow(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XMapRaised(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XMapRaised");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XMapRaised(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XUnmapWindow(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XUnmapWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XUnmapWindow(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XRaiseWindow(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XRaiseWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XRaiseWindow(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XLowerWindow(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XLowerWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XLowerWindow(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XMoveWindow(Display *d, Window w, int x, int y)
{
	static int (*real)(Display *, Window, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, int, int))X11_FN("XMoveWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, x, y) : -1;
	xlog_ts(&ts, "XMoveWindow(d=%p,w=0x%lx,x=%d,y=%d) -> %d", (void *)d, (unsigned long)w, x, y, r);
	return r;
}

int XResizeWindow(Display *d, Window w, unsigned int wd, unsigned int h)
{
	static int (*real)(Display *, Window, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, unsigned int, unsigned int))X11_FN("XResizeWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, wd, h) : -1;
	xlog_ts(&ts, "XResizeWindow(d=%p,w=0x%lx,w=%u,h=%u) -> %d", (void *)d, (unsigned long)w, wd, h, r);
	return r;
}

int XMoveResizeWindow(Display *d, Window w, int x, int y, unsigned int wd, unsigned int h)
{
	static int (*real)(Display *, Window, int, int, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, int, int, unsigned int, unsigned int))X11_FN("XMoveResizeWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, x, y, wd, h) : -1;
	xlog_ts(&ts, "XMoveResizeWindow(d=%p,w=0x%lx,x=%d,y=%d,w=%u,h=%u) -> %d",
		(void *)d, (unsigned long)w, x, y, wd, h, r);
	return r;
}

int XConfigureWindow(Display *d, Window w, unsigned int mask, XWindowChanges *values)
{
	static int (*real)(Display *, Window, unsigned int, XWindowChanges *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, unsigned int, XWindowChanges *))X11_FN("XConfigureWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, mask, values) : -1;
	xlog_ts(&ts, "XConfigureWindow(d=%p,w=0x%lx,mask=0x%x,values=%p) -> %d",
		(void *)d, (unsigned long)w, mask, (void *)values, r);
	return r;
}

int XChangeWindowAttributes(Display *d, Window w, unsigned long mask, XSetWindowAttributes *attrs)
{
	static int (*real)(Display *, Window, unsigned long, XSetWindowAttributes *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, unsigned long, XSetWindowAttributes *))X11_FN("XChangeWindowAttributes");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, mask, attrs) : -1;
	xlog_ts(&ts, "XChangeWindowAttributes(d=%p,w=0x%lx,mask=0x%lx,attrs=%p) -> %d",
		(void *)d, (unsigned long)w, mask, (void *)attrs, r);
	return r;
}

int XReparentWindow(Display *d, Window w, Window parent, int x, int y)
{
	static int (*real)(Display *, Window, Window, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Window, int, int))X11_FN("XReparentWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, parent, x, y) : -1;
	xlog_ts(&ts, "XReparentWindow(d=%p,w=0x%lx,parent=0x%lx,x=%d,y=%d) -> %d",
		(void *)d, (unsigned long)w, (unsigned long)parent, x, y, r);
	return r;
}

int XSetWindowBackground(Display *d, Window w, unsigned long pixel)
{
	static int (*real)(Display *, Window, unsigned long) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, unsigned long))X11_FN("XSetWindowBackground");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, pixel) : -1;
	xlog_ts(&ts, "XSetWindowBackground(d=%p,w=0x%lx,pixel=0x%lx) -> %d", (void *)d, (unsigned long)w, pixel, r);
	return r;
}

int XSetWindowBackgroundPixmap(Display *d, Window w, Pixmap p)
{
	static int (*real)(Display *, Window, Pixmap) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Pixmap))X11_FN("XSetWindowBackgroundPixmap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, p) : -1;
	xlog_ts(&ts, "XSetWindowBackgroundPixmap(d=%p,w=0x%lx,pixmap=0x%lx) -> %d", (void *)d, (unsigned long)w, (unsigned long)p, r);
	return r;
}

int XSetWindowBorder(Display *d, Window w, unsigned long pixel)
{
	static int (*real)(Display *, Window, unsigned long) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, unsigned long))X11_FN("XSetWindowBorder");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, pixel) : -1;
	xlog_ts(&ts, "XSetWindowBorder(d=%p,w=0x%lx,pixel=0x%lx) -> %d", (void *)d, (unsigned long)w, pixel, r);
	return r;
}

int XSetWindowBorderWidth(Display *d, Window w, unsigned int width)
{
	static int (*real)(Display *, Window, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, unsigned int))X11_FN("XSetWindowBorderWidth");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, width) : -1;
	xlog_ts(&ts, "XSetWindowBorderWidth(d=%p,w=0x%lx,width=%u) -> %d", (void *)d, (unsigned long)w, width, r);
	return r;
}

int XSetWindowBorderPixmap(Display *d, Window w, Pixmap p)
{
	static int (*real)(Display *, Window, Pixmap) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Pixmap))X11_FN("XSetWindowBorderPixmap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, p) : -1;
	xlog_ts(&ts, "XSetWindowBorderPixmap(d=%p,w=0x%lx,pixmap=0x%lx) -> %d", (void *)d, (unsigned long)w, (unsigned long)p, r);
	return r;
}

int XClearWindow(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XClearWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XClearWindow(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XClearArea(Display *d, Window w, int x, int y, unsigned int wd, unsigned int h, Bool exposures)
{
	static int (*real)(Display *, Window, int, int, unsigned int, unsigned int, Bool) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, int, int, unsigned int, unsigned int, Bool))X11_FN("XClearArea");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, x, y, wd, h, exposures) : -1;
	xlog_ts(&ts, "XClearArea(d=%p,w=0x%lx,x=%d,y=%d,w=%u,h=%u,exposures=%d) -> %d",
		(void *)d, (unsigned long)w, x, y, wd, h, (int)exposures, r);
	return r;
}

int XSetTransientForHint(Display *d, Window w, Window prop_window)
{
	static int (*real)(Display *, Window, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Window))X11_FN("XSetTransientForHint");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, prop_window) : -1;
	xlog_ts(&ts, "XSetTransientForHint(d=%p,w=0x%lx,prop_window=0x%lx) -> %d",
		(void *)d, (unsigned long)w, (unsigned long)prop_window, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* window query                                                        */
/* ------------------------------------------------------------------ */

Status XGetWindowAttributes(Display *d, Window w, XWindowAttributes *attr)
{
	static Status (*real)(Display *, Window, XWindowAttributes *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, XWindowAttributes *))X11_FN("XGetWindowAttributes");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, attr) : 0;
	xlog_ts(&ts, "XGetWindowAttributes(d=%p,w=0x%lx) -> %d attr={map_state=%d,w=%d,h=%d,class=%d,depth=%d,visual=%p,border_width=%d,all_event_masks=0x%lx,your_event_mask=0x%lx}",
		(void *)d, (unsigned long)w, (int)r,
		attr ? (int)attr->map_state : -1, attr ? (int)attr->width : -1,
		attr ? (int)attr->height : -1, attr ? (int)attr->class : -1,
		attr ? (int)attr->depth : -1, attr ? (void *)attr->visual : NULL,
		attr ? (int)attr->border_width : -1, attr ? (unsigned long)attr->all_event_masks : 0,
		attr ? (unsigned long)attr->your_event_mask : 0);
	return r;
}

Status XGetGeometry(Display *d, Drawable dw, Window *root, int *x, int *y,
		    unsigned int *wd, unsigned int *h, unsigned int *bw, unsigned int *depth)
{
	static Status (*real)(Display *, Drawable, Window *, int *, int *, unsigned int *,
			      unsigned int *, unsigned int *, unsigned int *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Drawable, Window *, int *, int *, unsigned int *,
				 unsigned int *, unsigned int *, unsigned int *))X11_FN("XGetGeometry");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, dw, root, x, y, wd, h, bw, depth) : 0;
	xlog_ts(&ts, "XGetGeometry(d=%p,drawable=0x%lx) -> %d root=0x%lx x=%d y=%d w=%u h=%u bw=%u depth=%u",
		(void *)d, (unsigned long)dw, (int)r,
		root ? (unsigned long)*root : 0, x ? *x : -1, y ? *y : -1,
		wd ? *wd : 0, h ? *h : 0, bw ? *bw : 0, depth ? *depth : 0);
	return r;
}

Status XQueryTree(Display *d, Window w, Window *root, Window *parent,
		  Window **children, unsigned int *nchildren)
{
	static Status (*real)(Display *, Window, Window *, Window *, Window **, unsigned int *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, Window *, Window *, Window **, unsigned int *))X11_FN("XQueryTree");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, root, parent, children, nchildren) : 0;
	xlog_ts(&ts, "XQueryTree(d=%p,w=0x%lx) -> %d root=0x%lx parent=0x%lx nchildren=%u",
		(void *)d, (unsigned long)w, (int)r,
		root ? (unsigned long)*root : 0, parent ? (unsigned long)*parent : 0,
		nchildren ? *nchildren : 0);
	return r;
}

int XGetWindowProperty(Display *d, Window w, Atom property, long offset, long length,
		       Bool del, Atom req_type, Atom *actual_type, int *actual_format,
		       unsigned long *nitems, unsigned long *bytes_after, unsigned char **prop)
{
	static int (*real)(Display *, Window, Atom, long, long, Bool, Atom, Atom *, int *,
			   unsigned long *, unsigned long *, unsigned char **) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Atom, long, long, Bool, Atom, Atom *, int *,
			       unsigned long *, unsigned long *, unsigned char **))X11_FN("XGetWindowProperty");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, property, offset, length, del, req_type,
			    actual_type, actual_format, nitems, bytes_after, prop) : -1;
	xlog_ts(&ts, "XGetWindowProperty(d=%p,w=0x%lx,property=%s,offset=%ld,length=%ld,delete=%d,req_type=%s) -> %d actual_type=%s format=%d nitems=%lu",
		(void *)d, (unsigned long)w, atom_name(d, property), offset, length, (int)del,
		atom_name(d, req_type), r,
		actual_type ? atom_name(d, *actual_type) : "?", actual_format ? *actual_format : -1,
		nitems ? *nitems : 0);
	return r;
}

Bool XTranslateCoordinates(Display *d, Window src, Window dst, int sx, int sy,
			  int *dx, int *dy, Window *child)
{
	static Bool (*real)(Display *, Window, Window, int, int, int *, int *, Window *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, Window, Window, int, int, int *, int *, Window *))X11_FN("XTranslateCoordinates");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, src, dst, sx, sy, dx, dy, child) : 0;
	xlog_ts(&ts, "XTranslateCoordinates(d=%p,src=0x%lx,dst=0x%lx,x=%d,y=%d) -> %d dx=%d dy=%d child=0x%lx",
		(void *)d, (unsigned long)src, (unsigned long)dst, sx, sy, (int)r,
		dx ? *dx : -1, dy ? *dy : -1, child ? (unsigned long)*child : 0);
	return r;
}

Bool XQueryPointer(Display *d, Window w, Window *root, Window *child, int *rx, int *ry,
		   int *wx, int *wy, unsigned int *mask)
{
	static Bool (*real)(Display *, Window, Window *, Window *, int *, int *, int *, int *,
			    unsigned int *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, Window, Window *, Window *, int *, int *, int *, int *,
				unsigned int *))X11_FN("XQueryPointer");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, w, root, child, rx, ry, wx, wy, mask) : 0;
	xlog_ts(&ts, "XQueryPointer(d=%p,w=0x%lx) -> %d root=0x%lx child=0x%lx root_x=%d root_y=%d win_x=%d win_y=%d mask=0x%x",
		(void *)d, (unsigned long)w, (int)r,
		root ? (unsigned long)*root : 0, child ? (unsigned long)*child : 0,
		rx ? *rx : -1, ry ? *ry : -1, wx ? *wx : -1, wy ? *wy : -1,
		mask ? *mask : 0);
	return r;
}

int XGetInputFocus(Display *d, Window *focus, int *revert)
{
	static int (*real)(Display *, Window *, int *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window *, int *))X11_FN("XGetInputFocus");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, focus, revert) : -1;
	xlog_ts(&ts, "XGetInputFocus(d=%p) -> %d focus=0x%lx revert_to=%d",
		(void *)d, r, focus ? (unsigned long)*focus : 0, revert ? *revert : -1);
	return r;
}

Status XGetWMProtocols(Display *d, Window w, Atom **protos, int *count)
{
	static Status (*real)(Display *, Window, Atom **, int *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, Atom **, int *))X11_FN("XGetWMProtocols");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, protos, count) : 0;
	xlog_ts(&ts, "XGetWMProtocols(d=%p,w=0x%lx) -> %d count=%d",
		(void *)d, (unsigned long)w, (int)r, count ? *count : -1);
	return r;
}

void XSetWMNormalHints(Display *d, Window w, XSizeHints *hints)
{
	static void (*real)(Display *, Window, XSizeHints *) = NULL;
	if (!real)
		real = (void(*)(Display *, Window, XSizeHints *))X11_FN("XSetWMNormalHints");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(d, w, hints);
	xlog_ts(&ts, "XSetWMNormalHints(d=%p,w=0x%lx,hints=%p{flags=0x%lx,x=%d,y=%d,w=%d,h=%d,min_w=%d,min_h=%d})",
		(void *)d, (unsigned long)w, (void *)hints,
		hints ? (unsigned long)hints->flags : 0,
		hints ? hints->x : -1, hints ? hints->y : -1,
		hints ? hints->width : -1, hints ? hints->height : -1,
		hints ? hints->min_width : -1, hints ? hints->min_height : -1);
}

int XSetWMHints(Display *d, Window w, XWMHints *wmhints)
{
	static int (*real)(Display *, Window, XWMHints *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, XWMHints *))X11_FN("XSetWMHints");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, wmhints) : -1;
	xlog_ts(&ts, "XSetWMHints(d=%p,w=0x%lx,wmhints=%p{flags=0x%lx,input=%d,initial_state=%d,window_group=0x%lx}) -> %d",
		(void *)d, (unsigned long)w, (void *)wmhints,
		wmhints ? (unsigned long)wmhints->flags : 0,
		wmhints ? (int)wmhints->input : -1,
		wmhints ? (int)wmhints->initial_state : -1,
		wmhints ? (unsigned long)wmhints->window_group : 0, r);
	return r;
}

void XSetWMProperties(Display *d, Window w, XTextProperty *name, XTextProperty *icon,
		      char **argv, int argc, XSizeHints *nhints, XWMHints *wmhints,
		      XClassHint *classhints)
{
	static void (*real)(Display *, Window, XTextProperty *, XTextProperty *, char **, int,
			    XSizeHints *, XWMHints *, XClassHint *) = NULL;
	if (!real)
		real = (void(*)(Display *, Window, XTextProperty *, XTextProperty *, char **, int,
			       XSizeHints *, XWMHints *, XClassHint *))X11_FN("XSetWMProperties");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(d, w, name, icon, argv, argc, nhints, wmhints, classhints);
	xlog_ts(&ts, "XSetWMProperties(d=%p,w=0x%lx,name=%p{value=%s},icon=%p,argc=%d,classhints=%p{res_name=%s,res_class=%s})",
		(void *)d, (unsigned long)w, (void *)name,
		(name && name->value) ? qstr((const char *)name->value, (int)name->nitems) : "(null)",
		(void *)icon, argc, (void *)classhints,
		classhints && classhints->res_name ? classhints->res_name : "(null)",
		classhints && classhints->res_class ? classhints->res_class : "(null)");
}

/* ------------------------------------------------------------------ */
/* GC                                                                  */
/* ------------------------------------------------------------------ */

GC XCreateGC(Display *d, Drawable dw, unsigned long mask, XGCValues *values)
{
	static GC (*real)(Display *, Drawable, unsigned long, XGCValues *) = NULL;
	if (!real)
		real = (GC(*)(Display *, Drawable, unsigned long, XGCValues *))X11_FN("XCreateGC");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	GC r = real ? real(d, dw, mask, values) : NULL;
	xlog_ts(&ts, "XCreateGC(d=%p,drawable=0x%lx,mask=0x%lx,values=%p) -> %p",
		(void *)d, (unsigned long)dw, mask, (void *)values, (void *)r);
	return r;
}

int XFreeGC(Display *d, GC gc)
{
	static int (*real)(Display *, GC) = NULL;
	if (!real)
		real = (int(*)(Display *, GC))X11_FN("XFreeGC");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc) : -1;
	xlog_ts(&ts, "XFreeGC(d=%p,gc=%p) -> %d", (void *)d, (void *)gc, r);
	return r;
}

int XChangeGC(Display *d, GC gc, unsigned long mask, XGCValues *values)
{
	static int (*real)(Display *, GC, unsigned long, XGCValues *) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, unsigned long, XGCValues *))X11_FN("XChangeGC");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, mask, values) : -1;
	xlog_ts(&ts, "XChangeGC(d=%p,gc=%p,mask=0x%lx,values=%p) -> %d",
		(void *)d, (void *)gc, mask, (void *)values, r);
	return r;
}

int XCopyGC(Display *d, GC src, unsigned long mask, GC dst)
{
	static int (*real)(Display *, GC, unsigned long, GC) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, unsigned long, GC))X11_FN("XCopyGC");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, src, mask, dst) : -1;
	xlog_ts(&ts, "XCopyGC(d=%p,src=%p,mask=0x%lx,dst=%p) -> %d", (void *)d, (void *)src, mask, (void *)dst, r);
	return r;
}

int XSetForeground(Display *d, GC gc, unsigned long pixel)
{
	static int (*real)(Display *, GC, unsigned long) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, unsigned long))X11_FN("XSetForeground");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, pixel) : -1;
	xlog_ts(&ts, "XSetForeground(d=%p,gc=%p,pixel=0x%lx) -> %d", (void *)d, (void *)gc, pixel, r);
	return r;
}

int XSetBackground(Display *d, GC gc, unsigned long pixel)
{
	static int (*real)(Display *, GC, unsigned long) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, unsigned long))X11_FN("XSetBackground");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, pixel) : -1;
	xlog_ts(&ts, "XSetBackground(d=%p,gc=%p,pixel=0x%lx) -> %d", (void *)d, (void *)gc, pixel, r);
	return r;
}

int XSetFillStyle(Display *d, GC gc, int style)
{
	static int (*real)(Display *, GC, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int))X11_FN("XSetFillStyle");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, style) : -1;
	xlog_ts(&ts, "XSetFillStyle(d=%p,gc=%p,fill_style=%d) -> %d", (void *)d, (void *)gc, style, r);
	return r;
}

int XSetFillRule(Display *d, GC gc, int rule)
{
	static int (*real)(Display *, GC, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int))X11_FN("XSetFillRule");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, rule) : -1;
	xlog_ts(&ts, "XSetFillRule(d=%p,gc=%p,fill_rule=%d) -> %d", (void *)d, (void *)gc, rule, r);
	return r;
}

int XSetFunction(Display *d, GC gc, int func)
{
	static int (*real)(Display *, GC, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int))X11_FN("XSetFunction");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, func) : -1;
	xlog_ts(&ts, "XSetFunction(d=%p,gc=%p,function=%d) -> %d", (void *)d, (void *)gc, func, r);
	return r;
}

int XSetLineAttributes(Display *d, GC gc, unsigned int lw, int ls, int cap, int join)
{
	static int (*real)(Display *, GC, unsigned int, int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, unsigned int, int, int, int))X11_FN("XSetLineAttributes");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, lw, ls, cap, join) : -1;
	xlog_ts(&ts, "XSetLineAttributes(d=%p,gc=%p,line_width=%u,line_style=%d,cap_style=%d,join_style=%d) -> %d",
		(void *)d, (void *)gc, lw, ls, cap, join, r);
	return r;
}

int XSetFont(Display *d, GC gc, Font f)
{
	static int (*real)(Display *, GC, Font) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, Font))X11_FN("XSetFont");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, f) : -1;
	xlog_ts(&ts, "XSetFont(d=%p,gc=%p,font=0x%lx) -> %d", (void *)d, (void *)gc, (unsigned long)f, r);
	return r;
}

int XSetSubwindowMode(Display *d, GC gc, int mode)
{
	static int (*real)(Display *, GC, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int))X11_FN("XSetSubwindowMode");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, mode) : -1;
	xlog_ts(&ts, "XSetSubwindowMode(d=%p,gc=%p,subwindow_mode=%d) -> %d", (void *)d, (void *)gc, mode, r);
	return r;
}

int XSetGraphicsExposures(Display *d, GC gc, Bool ge)
{
	static int (*real)(Display *, GC, Bool) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, Bool))X11_FN("XSetGraphicsExposures");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, ge) : -1;
	xlog_ts(&ts, "XSetGraphicsExposures(d=%p,gc=%p,graphics_exposures=%d) -> %d", (void *)d, (void *)gc, (int)ge, r);
	return r;
}

int XSetClipMask(Display *d, GC gc, Pixmap p)
{
	static int (*real)(Display *, GC, Pixmap) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, Pixmap))X11_FN("XSetClipMask");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, p) : -1;
	xlog_ts(&ts, "XSetClipMask(d=%p,gc=%p,pixmap=0x%lx) -> %d", (void *)d, (void *)gc, (unsigned long)p, r);
	return r;
}

int XSetClipOrigin(Display *d, GC gc, int x, int y)
{
	static int (*real)(Display *, GC, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int, int))X11_FN("XSetClipOrigin");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, x, y) : -1;
	xlog_ts(&ts, "XSetClipOrigin(d=%p,gc=%p,clip_x_origin=%d,clip_y_origin=%d) -> %d", (void *)d, (void *)gc, x, y, r);
	return r;
}

int XSetClipRectangles(Display *d, GC gc, int cx, int cy, XRectangle *rects, int n, int ordering)
{
	static int (*real)(Display *, GC, int, int, XRectangle *, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int, int, XRectangle *, int, int))X11_FN("XSetClipRectangles");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, cx, cy, rects, n, ordering) : -1;
	xlog_ts(&ts, "XSetClipRectangles(d=%p,gc=%p,clip_x=%d,clip_y=%d,rects=%p,n=%d,ordering=%d) -> %d",
		(void *)d, (void *)gc, cx, cy, (void *)rects, n, ordering, r);
	return r;
}

int XSetArcMode(Display *d, GC gc, int mode)
{
	static int (*real)(Display *, GC, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int))X11_FN("XSetArcMode");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, mode) : -1;
	xlog_ts(&ts, "XSetArcMode(d=%p,gc=%p,arc_mode=%d) -> %d", (void *)d, (void *)gc, mode, r);
	return r;
}

int XSetTile(Display *d, GC gc, Pixmap p)
{
	static int (*real)(Display *, GC, Pixmap) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, Pixmap))X11_FN("XSetTile");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, p) : -1;
	xlog_ts(&ts, "XSetTile(d=%p,gc=%p,pixmap=0x%lx) -> %d", (void *)d, (void *)gc, (unsigned long)p, r);
	return r;
}

int XSetStipple(Display *d, GC gc, Pixmap p)
{
	static int (*real)(Display *, GC, Pixmap) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, Pixmap))X11_FN("XSetStipple");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, p) : -1;
	xlog_ts(&ts, "XSetStipple(d=%p,gc=%p,pixmap=0x%lx) -> %d", (void *)d, (void *)gc, (unsigned long)p, r);
	return r;
}

int XSetTSOrigin(Display *d, GC gc, int x, int y)
{
	static int (*real)(Display *, GC, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int, int))X11_FN("XSetTSOrigin");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, x, y) : -1;
	xlog_ts(&ts, "XSetTSOrigin(d=%p,gc=%p,ts_x_origin=%d,ts_y_origin=%d) -> %d", (void *)d, (void *)gc, x, y, r);
	return r;
}

int XSetDashes(Display *d, GC gc, int offset, const char *dash_list, int n)
{
	static int (*real)(Display *, GC, int, const char *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, int, const char *, int))X11_FN("XSetDashes");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, gc, offset, dash_list, n) : -1;
	xlog_ts(&ts, "XSetDashes(d=%p,gc=%p,dash_offset=%d,dash_list=%s,n=%d) -> %d",
		(void *)d, (void *)gc, offset, qstr(dash_list, n), n, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* drawing                                                             */
/* ------------------------------------------------------------------ */

int XDrawPoint(Display *d, Drawable dw, GC gc, int x, int y)
{
	static int (*real)(Display *, Drawable, GC, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int))X11_FN("XDrawPoint");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y) : -1;
	xlog_ts(&ts, "XDrawPoint(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d) -> %d", (void *)d, (unsigned long)dw, (void *)gc, x, y, r);
	return r;
}

int XDrawPoints(Display *d, Drawable dw, GC gc, XPoint *pts, int n, int mode)
{
	static int (*real)(Display *, Drawable, GC, XPoint *, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XPoint *, int, int))X11_FN("XDrawPoints");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, pts, n, mode) : -1;
	xlog_ts(&ts, "XDrawPoints(d=%p,drawable=0x%lx,gc=%p,points=%p,n=%d,mode=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)pts, n, mode, r);
	return r;
}

int XDrawLine(Display *d, Drawable dw, GC gc, int x1, int y1, int x2, int y2)
{
	static int (*real)(Display *, Drawable, GC, int, int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, int, int))X11_FN("XDrawLine");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x1, y1, x2, y2) : -1;
	xlog_ts(&ts, "XDrawLine(d=%p,drawable=0x%lx,gc=%p,x1=%d,y1=%d,x2=%d,y2=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x1, y1, x2, y2, r);
	return r;
}

int XDrawLines(Display *d, Drawable dw, GC gc, XPoint *pts, int n, int mode)
{
	static int (*real)(Display *, Drawable, GC, XPoint *, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XPoint *, int, int))X11_FN("XDrawLines");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, pts, n, mode) : -1;
	xlog_ts(&ts, "XDrawLines(d=%p,drawable=0x%lx,gc=%p,points=%p,n=%d,mode=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)pts, n, mode, r);
	return r;
}

int XDrawSegments(Display *d, Drawable dw, GC gc, XSegment *segs, int n)
{
	static int (*real)(Display *, Drawable, GC, XSegment *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XSegment *, int))X11_FN("XDrawSegments");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, segs, n) : -1;
	xlog_ts(&ts, "XDrawSegments(d=%p,drawable=0x%lx,gc=%p,segments=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)segs, n, r);
	return r;
}

int XDrawRectangle(Display *d, Drawable dw, GC gc, int x, int y, unsigned int w, unsigned int h)
{
	static int (*real)(Display *, Drawable, GC, int, int, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, unsigned int, unsigned int))X11_FN("XDrawRectangle");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, w, h) : -1;
	xlog_ts(&ts, "XDrawRectangle(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,w=%u,h=%u) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, w, h, r);
	return r;
}

int XDrawRectangles(Display *d, Drawable dw, GC gc, XRectangle *rects, int n)
{
	static int (*real)(Display *, Drawable, GC, XRectangle *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XRectangle *, int))X11_FN("XDrawRectangles");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, rects, n) : -1;
	xlog_ts(&ts, "XDrawRectangles(d=%p,drawable=0x%lx,gc=%p,rectangles=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)rects, n, r);
	return r;
}

int XDrawArc(Display *d, Drawable dw, GC gc, int x, int y, unsigned int w, unsigned int h,
	     int a1, int a2)
{
	static int (*real)(Display *, Drawable, GC, int, int, unsigned int, unsigned int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, unsigned int, unsigned int, int, int))X11_FN("XDrawArc");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, w, h, a1, a2) : -1;
	xlog_ts(&ts, "XDrawArc(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,w=%u,h=%u,angle1=%d,angle2=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, w, h, a1, a2, r);
	return r;
}

int XDrawArcs(Display *d, Drawable dw, GC gc, XArc *arcs, int n)
{
	static int (*real)(Display *, Drawable, GC, XArc *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XArc *, int))X11_FN("XDrawArcs");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, arcs, n) : -1;
	xlog_ts(&ts, "XDrawArcs(d=%p,drawable=0x%lx,gc=%p,arcs=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)arcs, n, r);
	return r;
}

int XFillRectangle(Display *d, Drawable dw, GC gc, int x, int y, unsigned int w, unsigned int h)
{
	static int (*real)(Display *, Drawable, GC, int, int, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, unsigned int, unsigned int))X11_FN("XFillRectangle");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, w, h) : -1;
	xlog_ts(&ts, "XFillRectangle(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,w=%u,h=%u) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, w, h, r);
	return r;
}

int XFillRectangles(Display *d, Drawable dw, GC gc, XRectangle *rects, int n)
{
	static int (*real)(Display *, Drawable, GC, XRectangle *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XRectangle *, int))X11_FN("XFillRectangles");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, rects, n) : -1;
	xlog_ts(&ts, "XFillRectangles(d=%p,drawable=0x%lx,gc=%p,rectangles=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)rects, n, r);
	return r;
}

int XFillPolygon(Display *d, Drawable dw, GC gc, XPoint *pts, int n, int shape, int mode)
{
	static int (*real)(Display *, Drawable, GC, XPoint *, int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XPoint *, int, int, int))X11_FN("XFillPolygon");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, pts, n, shape, mode) : -1;
	xlog_ts(&ts, "XFillPolygon(d=%p,drawable=0x%lx,gc=%p,points=%p,n=%d,shape=%d,mode=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)pts, n, shape, mode, r);
	return r;
}

int XFillArc(Display *d, Drawable dw, GC gc, int x, int y, unsigned int w, unsigned int h,
	     int a1, int a2)
{
	static int (*real)(Display *, Drawable, GC, int, int, unsigned int, unsigned int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, unsigned int, unsigned int, int, int))X11_FN("XFillArc");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, w, h, a1, a2) : -1;
	xlog_ts(&ts, "XFillArc(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,w=%u,h=%u,angle1=%d,angle2=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, w, h, a1, a2, r);
	return r;
}

int XFillArcs(Display *d, Drawable dw, GC gc, XArc *arcs, int n)
{
	static int (*real)(Display *, Drawable, GC, XArc *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XArc *, int))X11_FN("XFillArcs");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, arcs, n) : -1;
	xlog_ts(&ts, "XFillArcs(d=%p,drawable=0x%lx,gc=%p,arcs=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)arcs, n, r);
	return r;
}

int XDrawString(Display *d, Drawable dw, GC gc, int x, int y, const char *s, int len)
{
	static int (*real)(Display *, Drawable, GC, int, int, const char *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, const char *, int))X11_FN("XDrawString");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, s, len) : -1;
	xlog_ts(&ts, "XDrawString(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,string=%s,len=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, qstr(s, len), len, r);
	return r;
}

int XDrawString16(Display *d, Drawable dw, GC gc, int x, int y, const XChar2b *s, int len)
{
	static int (*real)(Display *, Drawable, GC, int, int, const XChar2b *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, const XChar2b *, int))X11_FN("XDrawString16");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, s, len) : -1;
	xlog_ts(&ts, "XDrawString16(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,string16=%p,len=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, (const void *)s, len, r);
	return r;
}

int XDrawImageString(Display *d, Drawable dw, GC gc, int x, int y, const char *s, int len)
{
	static int (*real)(Display *, Drawable, GC, int, int, const char *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, const char *, int))X11_FN("XDrawImageString");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, s, len) : -1;
	xlog_ts(&ts, "XDrawImageString(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,string=%s,len=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, qstr(s, len), len, r);
	return r;
}

int XDrawImageString16(Display *d, Drawable dw, GC gc, int x, int y, const XChar2b *s, int len)
{
	static int (*real)(Display *, Drawable, GC, int, int, const XChar2b *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, const XChar2b *, int))X11_FN("XDrawImageString16");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, s, len) : -1;
	xlog_ts(&ts, "XDrawImageString16(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,string16=%p,len=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, (const void *)s, len, r);
	return r;
}

int XDrawText(Display *d, Drawable dw, GC gc, int x, int y, XTextItem *items, int n)
{
	static int (*real)(Display *, Drawable, GC, int, int, XTextItem *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, XTextItem *, int))X11_FN("XDrawText");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, items, n) : -1;
	xlog_ts(&ts, "XDrawText(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,items=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, (void *)items, n, r);
	return r;
}

int XDrawText16(Display *d, Drawable dw, GC gc, int x, int y, XTextItem16 *items, int n)
{
	static int (*real)(Display *, Drawable, GC, int, int, XTextItem16 *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, int, int, XTextItem16 *, int))X11_FN("XDrawText16");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, x, y, items, n) : -1;
	xlog_ts(&ts, "XDrawText16(d=%p,drawable=0x%lx,gc=%p,x=%d,y=%d,items16=%p,n=%d) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, x, y, (void *)items, n, r);
	return r;
}

int XCopyArea(Display *d, Drawable src, Drawable dst, GC gc, int sx, int sy,
	      unsigned int w, unsigned int h, int dx, int dy)
{
	static int (*real)(Display *, Drawable, Drawable, GC, int, int, unsigned int,
			   unsigned int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, Drawable, GC, int, int, unsigned int,
			      unsigned int, int, int))X11_FN("XCopyArea");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, src, dst, gc, sx, sy, w, h, dx, dy) : -1;
	xlog_ts(&ts, "XCopyArea(d=%p,src=0x%lx,dst=0x%lx,gc=%p,src_x=%d,src_y=%d,w=%u,h=%u,dest_x=%d,dest_y=%d) -> %d",
		(void *)d, (unsigned long)src, (unsigned long)dst, (void *)gc, sx, sy, w, h, dx, dy, r);
	return r;
}

int XCopyPlane(Display *d, Drawable src, Drawable dst, GC gc, int sx, int sy,
	       unsigned int w, unsigned int h, int dx, int dy, unsigned long plane)
{
	static int (*real)(Display *, Drawable, Drawable, GC, int, int, unsigned int,
			   unsigned int, int, int, unsigned long) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, Drawable, GC, int, int, unsigned int,
			      unsigned int, int, int, unsigned long))X11_FN("XCopyPlane");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, src, dst, gc, sx, sy, w, h, dx, dy, plane) : -1;
	xlog_ts(&ts, "XCopyPlane(d=%p,src=0x%lx,dst=0x%lx,gc=%p,src_x=%d,src_y=%d,w=%u,h=%u,dest_x=%d,dest_y=%d,plane=0x%lx) -> %d",
		(void *)d, (unsigned long)src, (unsigned long)dst, (void *)gc, sx, sy, w, h, dx, dy, plane, r);
	return r;
}

int XPutImage(Display *d, Drawable dw, GC gc, XImage *img, int sx, int sy,
	      int dx, int dy, unsigned int w, unsigned int h)
{
	static int (*real)(Display *, Drawable, GC, XImage *, int, int, int, int,
			   unsigned int, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Display *, Drawable, GC, XImage *, int, int, int, int,
			      unsigned int, unsigned int))X11_FN("XPutImage");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, dw, gc, img, sx, sy, dx, dy, w, h) : -1;
	xlog_ts(&ts, "XPutImage(d=%p,drawable=0x%lx,gc=%p,image=%p{width=%d,height=%d,depth=%d,bpp=%d,format=%d},src_x=%d,src_y=%d,dest_x=%d,dest_y=%d,w=%u,h=%u) -> %d",
		(void *)d, (unsigned long)dw, (void *)gc, (void *)img,
		img ? img->width : -1, img ? img->height : -1,
		img ? img->depth : -1, img ? img->bits_per_pixel : -1,
		img ? img->byte_order : -1, sx, sy, dx, dy, w, h, r);
	return r;
}

Pixmap XCreatePixmap(Display *d, Drawable dw, unsigned int w, unsigned int h, unsigned int depth)
{
	static Pixmap (*real)(Display *, Drawable, unsigned int, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (Pixmap(*)(Display *, Drawable, unsigned int, unsigned int, unsigned int))X11_FN("XCreatePixmap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Pixmap r = real ? real(d, dw, w, h, depth) : 0;
	xlog_ts(&ts, "XCreatePixmap(d=%p,drawable=0x%lx,w=%u,h=%u,depth=%u) -> 0x%lx",
		(void *)d, (unsigned long)dw, w, h, depth, (unsigned long)r);
	return r;
}

int XFreePixmap(Display *d, Pixmap p)
{
	static int (*real)(Display *, Pixmap) = NULL;
	if (!real)
		real = (int(*)(Display *, Pixmap))X11_FN("XFreePixmap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, p) : -1;
	xlog_ts(&ts, "XFreePixmap(d=%p,pixmap=0x%lx) -> %d", (void *)d, (unsigned long)p, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* colour                                                              */
/* ------------------------------------------------------------------ */

Status XAllocColor(Display *d, Colormap cmap, XColor *c)
{
	static Status (*real)(Display *, Colormap, XColor *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Colormap, XColor *))X11_FN("XAllocColor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, cmap, c) : 0;
	xlog_ts(&ts, "XAllocColor(d=%p,cmap=0x%lx,color=%p{red=%u,green=%u,blue=%u,pixel=0x%lx}) -> %d",
		(void *)d, (unsigned long)cmap, (void *)c,
		c ? (unsigned int)c->red : 0, c ? (unsigned int)c->green : 0,
		c ? (unsigned int)c->blue : 0, c ? (unsigned long)c->pixel : 0, (int)r);
	return r;
}

int XFreeColors(Display *d, Colormap cmap, unsigned long *pixels, int n, unsigned long planes)
{
	static int (*real)(Display *, Colormap, unsigned long *, int, unsigned long) = NULL;
	if (!real)
		real = (int(*)(Display *, Colormap, unsigned long *, int, unsigned long))X11_FN("XFreeColors");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, cmap, pixels, n, planes) : -1;
	xlog_ts(&ts, "XFreeColors(d=%p,cmap=0x%lx,pixels=%p,n=%d,planes=0x%lx) -> %d",
		(void *)d, (unsigned long)cmap, (void *)pixels, n, planes, r);
	return r;
}

Status XLookupColor(Display *d, Colormap cmap, const char *name, XColor *exact, XColor *screen)
{
	static Status (*real)(Display *, Colormap, const char *, XColor *, XColor *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Colormap, const char *, XColor *, XColor *))X11_FN("XLookupColor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, cmap, name, exact, screen) : 0;
	xlog_ts(&ts, "XLookupColor(d=%p,cmap=0x%lx,name=%s) -> %d exact={pixel=0x%lx} screen={pixel=0x%lx}",
		(void *)d, (unsigned long)cmap, qstr(name, -1), (int)r,
		exact ? (unsigned long)exact->pixel : 0, screen ? (unsigned long)screen->pixel : 0);
	return r;
}

Status XParseColor(Display *d, Colormap cmap, const char *spec, XColor *out)
{
	static Status (*real)(Display *, Colormap, const char *, XColor *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Colormap, const char *, XColor *))X11_FN("XParseColor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, cmap, spec, out) : 0;
	xlog_ts(&ts, "XParseColor(d=%p,cmap=0x%lx,spec=%s) -> %d out={red=%u,green=%u,blue=%u}",
		(void *)d, (unsigned long)cmap, qstr(spec, -1), (int)r,
		out ? (unsigned int)out->red : 0, out ? (unsigned int)out->green : 0,
		out ? (unsigned int)out->blue : 0);
	return r;
}

Colormap XCreateColormap(Display *d, Window w, Visual *vis, int alloc)
{
	static Colormap (*real)(Display *, Window, Visual *, int) = NULL;
	if (!real)
		real = (Colormap(*)(Display *, Window, Visual *, int))X11_FN("XCreateColormap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Colormap r = real ? real(d, w, vis, alloc) : 0;
	xlog_ts(&ts, "XCreateColormap(d=%p,w=0x%lx,visual=%p,alloc=%d) -> 0x%lx",
		(void *)d, (unsigned long)w, (void *)vis, alloc, (unsigned long)r);
	return r;
}

int XFreeColormap(Display *d, Colormap cmap)
{
	static int (*real)(Display *, Colormap) = NULL;
	if (!real)
		real = (int(*)(Display *, Colormap))X11_FN("XFreeColormap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, cmap) : -1;
	xlog_ts(&ts, "XFreeColormap(d=%p,cmap=0x%lx) -> %d", (void *)d, (unsigned long)cmap, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* cursors                                                             */
/* ------------------------------------------------------------------ */

Cursor XCreateFontCursor(Display *d, unsigned int shape)
{
	static Cursor (*real)(Display *, unsigned int) = NULL;
	if (!real)
		real = (Cursor(*)(Display *, unsigned int))X11_FN("XCreateFontCursor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Cursor r = real ? real(d, shape) : 0;
	xlog_ts(&ts, "XCreateFontCursor(d=%p,shape=%u) -> 0x%lx", (void *)d, shape, (unsigned long)r);
	return r;
}

Cursor XCreatePixmapCursor(Display *d, Pixmap src, Pixmap mask, XColor *fg, XColor *bg,
			   unsigned int x, unsigned int y)
{
	static Cursor (*real)(Display *, Pixmap, Pixmap, XColor *, XColor *, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (Cursor(*)(Display *, Pixmap, Pixmap, XColor *, XColor *, unsigned int, unsigned int))X11_FN("XCreatePixmapCursor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Cursor r = real ? real(d, src, mask, fg, bg, x, y) : 0;
	xlog_ts(&ts, "XCreatePixmapCursor(d=%p,source=0x%lx,mask=0x%lx,fg=%p,bg=%p,x=%u,y=%u) -> 0x%lx",
		(void *)d, (unsigned long)src, (unsigned long)mask, (void *)fg, (void *)bg, x, y, (unsigned long)r);
	return r;
}

int XDefineCursor(Display *d, Window w, Cursor cur)
{
	static int (*real)(Display *, Window, Cursor) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Cursor))X11_FN("XDefineCursor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, cur) : -1;
	xlog_ts(&ts, "XDefineCursor(d=%p,w=0x%lx,cursor=0x%lx) -> %d", (void *)d, (unsigned long)w, (unsigned long)cur, r);
	return r;
}

int XUndefineCursor(Display *d, Window w)
{
	static int (*real)(Display *, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, Window))X11_FN("XUndefineCursor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w) : -1;
	xlog_ts(&ts, "XUndefineCursor(d=%p,w=0x%lx) -> %d", (void *)d, (unsigned long)w, r);
	return r;
}

int XFreeCursor(Display *d, Cursor cur)
{
	static int (*real)(Display *, Cursor) = NULL;
	if (!real)
		real = (int(*)(Display *, Cursor))X11_FN("XFreeCursor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, cur) : -1;
	xlog_ts(&ts, "XFreeCursor(d=%p,cursor=0x%lx) -> %d", (void *)d, (unsigned long)cur, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* atoms / properties / WM hints                                       */
/* ------------------------------------------------------------------ */

Atom XInternAtom(Display *d, const char *name, Bool only_if_exists)
{
	static Atom (*real)(Display *, const char *, Bool) = NULL;
	if (!real)
		real = (Atom(*)(Display *, const char *, Bool))X11_FN("XInternAtom");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Atom r = real ? real(d, name, only_if_exists) : 0;
	xlog_ts(&ts, "XInternAtom(d=%p,name=%s,only_if_exists=%d) -> %s",
		(void *)d, qstr(name, -1), (int)only_if_exists, atom_name(d, r));
	return r;
}

int XChangeProperty(Display *d, Window w, Atom prop, Atom type, int format, int mode,
		    const unsigned char *data, int nelements)
{
	static int (*real)(Display *, Window, Atom, Atom, int, int, const unsigned char *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Atom, Atom, int, int, const unsigned char *, int))X11_FN("XChangeProperty");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, prop, type, format, mode, data, nelements) : -1;
	xlog_ts(&ts, "XChangeProperty(d=%p,w=0x%lx,property=%s,type=%s,format=%d,mode=%d,data=%s,nelements=%d) -> %d",
		(void *)d, (unsigned long)w, atom_name(d, prop), atom_name(d, type), format, mode,
		(format == 8 && data && nelements >= 0) ? qstr((const char *)data, nelements) : "(raw)",
		nelements, r);
	return r;
}

int XDeleteProperty(Display *d, Window w, Atom prop)
{
	static int (*real)(Display *, Window, Atom) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Atom))X11_FN("XDeleteProperty");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, prop) : -1;
	xlog_ts(&ts, "XDeleteProperty(d=%p,w=0x%lx,property=%s) -> %d", (void *)d, (unsigned long)w, atom_name(d, prop), r);
	return r;
}

int XStoreName(Display *d, Window w, const char *name)
{
	static int (*real)(Display *, Window, const char *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, const char *))X11_FN("XStoreName");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, name) : -1;
	xlog_ts(&ts, "XStoreName(d=%p,w=0x%lx,window_name=%s) -> %d", (void *)d, (unsigned long)w, qstr(name, -1), r);
	return r;
}

int XSetIconName(Display *d, Window w, const char *name)
{
	static int (*real)(Display *, Window, const char *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, const char *))X11_FN("XSetIconName");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, name) : -1;
	xlog_ts(&ts, "XSetIconName(d=%p,w=0x%lx,icon_name=%s) -> %d", (void *)d, (unsigned long)w, qstr(name, -1), r);
	return r;
}

Status XSetWMProtocols(Display *d, Window w, Atom *protos, int count)
{
	static Status (*real)(Display *, Window, Atom *, int) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, Atom *, int))X11_FN("XSetWMProtocols");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, protos, count) : 0;
	xlog_ts(&ts, "XSetWMProtocols(d=%p,w=0x%lx,protocols=%p,count=%d) -> %d",
		(void *)d, (unsigned long)w, (void *)protos, count, (int)r);
	return r;
}

void XSetWMName(Display *d, Window w, XTextProperty *prop)
{
	static void (*real)(Display *, Window, XTextProperty *) = NULL;
	if (!real)
		real = (void(*)(Display *, Window, XTextProperty *))X11_FN("XSetWMName");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(d, w, prop);
	xlog_ts(&ts, "XSetWMName(d=%p,w=0x%lx,text_prop=%p{value=%s,encoding=%s,format=%d,nitems=%lu})",
		(void *)d, (unsigned long)w, (void *)prop,
		(prop && prop->value) ? qstr((const char *)prop->value, (int)prop->nitems) : "(null)",
		prop ? atom_name(d, prop->encoding) : "?", prop ? prop->format : -1,
		prop ? prop->nitems : 0);
}

void XSetWMIconName(Display *d, Window w, XTextProperty *prop)
{
	static void (*real)(Display *, Window, XTextProperty *) = NULL;
	if (!real)
		real = (void(*)(Display *, Window, XTextProperty *))X11_FN("XSetWMIconName");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(d, w, prop);
	xlog_ts(&ts, "XSetWMIconName(d=%p,w=0x%lx,text_prop=%p{value=%s})",
		(void *)d, (unsigned long)w, (void *)prop,
		(prop && prop->value) ? qstr((const char *)prop->value, (int)prop->nitems) : "(null)");
}

int XSetClassHint(Display *d, Window w, XClassHint *hints)
{
	static int (*real)(Display *, Window, XClassHint *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, XClassHint *))X11_FN("XSetClassHint");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, hints) : -1;
	xlog_ts(&ts, "XSetClassHint(d=%p,w=0x%lx,class_hints=%p{res_name=%s,res_class=%s}) -> %d",
		(void *)d, (unsigned long)w, (void *)hints,
		hints && hints->res_name ? hints->res_name : "(null)",
		hints && hints->res_class ? hints->res_class : "(null)", r);
	return r;
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */

int XSelectInput(Display *d, Window w, long mask)
{
	static int (*real)(Display *, Window, long) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, long))X11_FN("XSelectInput");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, mask) : -1;
	xlog_ts(&ts, "XSelectInput(d=%p,w=0x%lx,event_mask=0x%lx) -> %d",
		(void *)d, (unsigned long)w, mask, r);
	return r;
}

Status XSendEvent(Display *d, Window w, Bool propagate, long mask, XEvent *ev)
{
	static Status (*real)(Display *, Window, Bool, long, XEvent *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, Bool, long, XEvent *))X11_FN("XSendEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, propagate, mask, ev) : 0;
	xlog_ts(&ts, "XSendEvent(d=%p,w=0x%lx,propagate=%d,event_mask=0x%lx,event=%p{type=%d(%s)}) -> %d",
		(void *)d, (unsigned long)w, (int)propagate, mask, (void *)ev,
		ev ? ev->type : -1, ev ? event_name(ev->type) : "?", (int)r);
	return r;
}

int XPending(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XPending");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XPending(d=%p) -> %d", (void *)d, r);
	return r;
}

int XEventsQueued(Display *d, int mode)
{
	static int (*real)(Display *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, int))X11_FN("XEventsQueued");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, mode) : -1;
	xlog_ts(&ts, "XEventsQueued(d=%p,mode=%d) -> %d", (void *)d, mode, r);
	return r;
}

int XNextEvent(Display *d, XEvent *ev)
{
	static int (*real)(Display *, XEvent *) = NULL;
	if (!real)
		real = (int(*)(Display *, XEvent *))X11_FN("XNextEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, ev) : -1;
	xlog_ts(&ts, "XNextEvent(d=%p) -> %d event={type=%d(%s),window=0x%lx}",
		(void *)d, r, ev ? ev->type : -1, ev ? event_name(ev->type) : "?",
		ev ? (unsigned long)ev->xany.window : 0);
	return r;
}

int XWindowEvent(Display *d, Window w, long mask, XEvent *ev)
{
	static int (*real)(Display *, Window, long, XEvent *) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, long, XEvent *))X11_FN("XWindowEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, mask, ev) : -1;
	xlog_ts(&ts, "XWindowEvent(d=%p,w=0x%lx,event_mask=0x%lx) -> %d event={type=%d(%s)}",
		(void *)d, (unsigned long)w, mask, r, ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

Bool XCheckWindowEvent(Display *d, Window w, long mask, XEvent *ev)
{
	static Bool (*real)(Display *, Window, long, XEvent *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, Window, long, XEvent *))X11_FN("XCheckWindowEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, w, mask, ev) : 0;
	xlog_ts(&ts, "XCheckWindowEvent(d=%p,w=0x%lx,event_mask=0x%lx) -> %d event={type=%d(%s)}",
		(void *)d, (unsigned long)w, mask, (int)r,
		ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

Bool XCheckIfEvent(Display *d, XEvent *ev,
		   Bool (*predicate)(Display *, XEvent *, XPointer), XPointer arg)
{
	static Bool (*real)(Display *, XEvent *, Bool(*)(Display *, XEvent *, XPointer), XPointer) = NULL;
	if (!real)
		real = (Bool(*)(Display *, XEvent *, Bool(*)(Display *, XEvent *, XPointer), XPointer))X11_FN("XCheckIfEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, ev, predicate, arg) : 0;
	xlog_ts(&ts, "XCheckIfEvent(d=%p,predicate=%p,arg=%p) -> %d event={type=%d(%s)}",
		(void *)d, (void *)predicate, (void *)arg, (int)r,
		ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

Bool XCheckTypedEvent(Display *d, int type, XEvent *ev)
{
	static Bool (*real)(Display *, int, XEvent *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, int, XEvent *))X11_FN("XCheckTypedEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, type, ev) : 0;
	xlog_ts(&ts, "XCheckTypedEvent(d=%p,event_type=%d(%s)) -> %d event={type=%d(%s)}",
		(void *)d, type, event_name(type), (int)r,
		ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

Bool XCheckTypedWindowEvent(Display *d, Window w, int type, XEvent *ev)
{
	static Bool (*real)(Display *, Window, int, XEvent *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, Window, int, XEvent *))X11_FN("XCheckTypedWindowEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, w, type, ev) : 0;
	xlog_ts(&ts, "XCheckTypedWindowEvent(d=%p,w=0x%lx,event_type=%d(%s)) -> %d event={type=%d(%s)}",
		(void *)d, (unsigned long)w, type, event_name(type), (int)r,
		ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

Status XIfEvent(Display *d, XEvent *ev,
		Bool (*predicate)(Display *, XEvent *, XPointer), XPointer arg)
{
	static Status (*real)(Display *, XEvent *, Bool(*)(Display *, XEvent *, XPointer), XPointer) = NULL;
	if (!real)
		real = (Status(*)(Display *, XEvent *, Bool(*)(Display *, XEvent *, XPointer), XPointer))X11_FN("XIfEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, ev, predicate, arg) : 0;
	xlog_ts(&ts, "XIfEvent(d=%p,predicate=%p,arg=%p) -> %d event={type=%d(%s)}",
		(void *)d, (void *)predicate, (void *)arg, (int)r,
		ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

int XMaskEvent(Display *d, long mask, XEvent *ev)
{
	static int (*real)(Display *, long, XEvent *) = NULL;
	if (!real)
		real = (int(*)(Display *, long, XEvent *))X11_FN("XMaskEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, mask, ev) : -1;
	xlog_ts(&ts, "XMaskEvent(d=%p,event_mask=0x%lx) -> %d event={type=%d(%s)}",
		(void *)d, mask, r, ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

int XPeekEvent(Display *d, XEvent *ev)
{
	static int (*real)(Display *, XEvent *) = NULL;
	if (!real)
		real = (int(*)(Display *, XEvent *))X11_FN("XPeekEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, ev) : -1;
	xlog_ts(&ts, "XPeekEvent(d=%p) -> %d event={type=%d(%s)}",
		(void *)d, r, ev ? ev->type : -1, ev ? event_name(ev->type) : "?");
	return r;
}

int XPutBackEvent(Display *d, XEvent *ev)
{
	static int (*real)(Display *, XEvent *) = NULL;
	if (!real)
		real = (int(*)(Display *, XEvent *))X11_FN("XPutBackEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, ev) : -1;
	xlog_ts(&ts, "XPutBackEvent(d=%p,event=%p{type=%d(%s)}) -> %d",
		(void *)d, (void *)ev, ev ? ev->type : -1, ev ? event_name(ev->type) : "?", r);
	return r;
}

int XFlush(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XFlush");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XFlush(d=%p) -> %d", (void *)d, r);
	return r;
}

int XSync(Display *d, Bool discard)
{
	static int (*real)(Display *, Bool) = NULL;
	if (!real)
		real = (int(*)(Display *, Bool))X11_FN("XSync");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, discard) : -1;
	xlog_ts(&ts, "XSync(d=%p,discard=%d) -> %d", (void *)d, (int)discard, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* focus / grabs                                                       */
/* ------------------------------------------------------------------ */

int XSetInputFocus(Display *d, Window focus, int revert_to, Time time)
{
	static int (*real)(Display *, Window, int, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, int, Time))X11_FN("XSetInputFocus");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, focus, revert_to, time) : -1;
	xlog_ts(&ts, "XSetInputFocus(d=%p,focus=0x%lx,revert_to=%d,time=%lu) -> %d",
		(void *)d, (unsigned long)focus, revert_to, (unsigned long)time, r);
	return r;
}

int XGrabPointer(Display *d, Window w, Bool owner_events, unsigned int mask,
		 int pointer_mode, int keyboard_mode, Window confine, Cursor cur, Time time)
{
	static int (*real)(Display *, Window, Bool, unsigned int, int, int, Window, Cursor, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Bool, unsigned int, int, int, Window, Cursor, Time))X11_FN("XGrabPointer");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, owner_events, mask, pointer_mode, keyboard_mode, confine, cur, time) : -1;
	xlog_ts(&ts, "XGrabPointer(d=%p,grab_window=0x%lx,owner_events=%d,event_mask=0x%x,pointer_mode=%d,keyboard_mode=%d,confine_to=0x%lx,cursor=0x%lx,time=%lu) -> %d",
		(void *)d, (unsigned long)w, (int)owner_events, mask, pointer_mode, keyboard_mode,
		(unsigned long)confine, (unsigned long)cur, (unsigned long)time, r);
	return r;
}

int XUngrabPointer(Display *d, Time time)
{
	static int (*real)(Display *, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Time))X11_FN("XUngrabPointer");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, time) : -1;
	xlog_ts(&ts, "XUngrabPointer(d=%p,time=%lu) -> %d", (void *)d, (unsigned long)time, r);
	return r;
}

int XGrabKeyboard(Display *d, Window w, Bool owner_events, int pointer_mode,
		  int keyboard_mode, Time time)
{
	static int (*real)(Display *, Window, Bool, int, int, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Bool, int, int, Time))X11_FN("XGrabKeyboard");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, owner_events, pointer_mode, keyboard_mode, time) : -1;
	xlog_ts(&ts, "XGrabKeyboard(d=%p,grab_window=0x%lx,owner_events=%d,pointer_mode=%d,keyboard_mode=%d,time=%lu) -> %d",
		(void *)d, (unsigned long)w, (int)owner_events, pointer_mode, keyboard_mode, (unsigned long)time, r);
	return r;
}

int XUngrabKeyboard(Display *d, Time time)
{
	static int (*real)(Display *, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Time))X11_FN("XUngrabKeyboard");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, time) : -1;
	xlog_ts(&ts, "XUngrabKeyboard(d=%p,time=%lu) -> %d", (void *)d, (unsigned long)time, r);
	return r;
}

int XGrabButton(Display *d, unsigned int button, unsigned int modifiers, Window w,
		Bool owner_events, unsigned int mask, int pointer_mode, int keyboard_mode,
		Window confine, Cursor cur)
{
	static int (*real)(Display *, unsigned int, unsigned int, Window, Bool, unsigned int,
			   int, int, Window, Cursor) = NULL;
	if (!real)
		real = (int(*)(Display *, unsigned int, unsigned int, Window, Bool, unsigned int,
			      int, int, Window, Cursor))X11_FN("XGrabButton");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, button, modifiers, w, owner_events, mask, pointer_mode,
			    keyboard_mode, confine, cur) : -1;
	xlog_ts(&ts, "XGrabButton(d=%p,button=%u,modifiers=0x%x,grab_window=0x%lx,owner_events=%d,event_mask=0x%x,pointer_mode=%d,keyboard_mode=%d,confine_to=0x%lx,cursor=0x%lx) -> %d",
		(void *)d, button, modifiers, (unsigned long)w, (int)owner_events, mask,
		pointer_mode, keyboard_mode, (unsigned long)confine, (unsigned long)cur, r);
	return r;
}

int XUngrabButton(Display *d, unsigned int button, unsigned int modifiers, Window w)
{
	static int (*real)(Display *, unsigned int, unsigned int, Window) = NULL;
	if (!real)
		real = (int(*)(Display *, unsigned int, unsigned int, Window))X11_FN("XUngrabButton");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, button, modifiers, w) : -1;
	xlog_ts(&ts, "XUngrabButton(d=%p,button=%u,modifiers=0x%x,grab_window=0x%lx) -> %d",
		(void *)d, button, modifiers, (unsigned long)w, r);
	return r;
}

/* ------------------------------------------------------------------ */
/* fonts                                                               */
/* ------------------------------------------------------------------ */

Font XLoadFont(Display *d, const char *name)
{
	static Font (*real)(Display *, const char *) = NULL;
	if (!real)
		real = (Font(*)(Display *, const char *))X11_FN("XLoadFont");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Font r = real ? real(d, name) : 0;
	xlog_ts(&ts, "XLoadFont(d=%p,name=%s) -> 0x%lx", (void *)d, qstr(name, -1), (unsigned long)r);
	return r;
}

XFontStruct *XLoadQueryFont(Display *d, const char *name)
{
	static XFontStruct *(*real)(Display *, const char *) = NULL;
	if (!real)
		real = (XFontStruct *(*)(Display *, const char *))X11_FN("XLoadQueryFont");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XFontStruct *r = real ? real(d, name) : NULL;
	xlog_ts(&ts, "XLoadQueryFont(d=%p,name=%s) -> %p{fid=0x%lx,ascent=%d,descent=%d,min_bounds={w=%d}}",
		(void *)d, qstr(name, -1), (void *)r,
		r ? (unsigned long)r->fid : 0, r ? r->ascent : -1, r ? r->descent : -1,
		r ? r->min_bounds.width : -1);
	return r;
}

XFontStruct *XQueryFont(Display *d, Font f)
{
	static XFontStruct *(*real)(Display *, Font) = NULL;
	if (!real)
		real = (XFontStruct *(*)(Display *, Font))X11_FN("XQueryFont");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XFontStruct *r = real ? real(d, f) : NULL;
	xlog_ts(&ts, "XQueryFont(d=%p,font=0x%lx) -> %p{fid=0x%lx,ascent=%d,descent=%d}",
		(void *)d, (unsigned long)f, (void *)r,
		r ? (unsigned long)r->fid : 0, r ? r->ascent : -1, r ? r->descent : -1);
	return r;
}

int XFreeFont(Display *d, XFontStruct *fs)
{
	static int (*real)(Display *, XFontStruct *) = NULL;
	if (!real)
		real = (int(*)(Display *, XFontStruct *))X11_FN("XFreeFont");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, fs) : -1;
	xlog_ts(&ts, "XFreeFont(d=%p,font_struct=%p) -> %d", (void *)d, (void *)fs, r);
	return r;
}

int XUnloadFont(Display *d, Font f)
{
	static int (*real)(Display *, Font) = NULL;
	if (!real)
		real = (int(*)(Display *, Font))X11_FN("XUnloadFont");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, f) : -1;
	xlog_ts(&ts, "XUnloadFont(d=%p,font=0x%lx) -> %d", (void *)d, (unsigned long)f, r);
	return r;
}

char **XListFonts(Display *d, const char *pattern, int max, int *count)
{
	static char **(*real)(Display *, const char *, int, int *) = NULL;
	if (!real)
		real = (char **(*)(Display *, const char *, int, int *))X11_FN("XListFonts");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char **r = real ? real(d, pattern, max, count) : NULL;
	xlog_ts(&ts, "XListFonts(d=%p,pattern=%s,max_names=%d) -> %p count=%d",
		(void *)d, qstr(pattern, -1), max, (void *)r, count ? *count : -1);
	return r;
}

Bool XGetFontProperty(XFontStruct *fs, Atom atom, unsigned long *value)
{
	static Bool (*real)(XFontStruct *, Atom, unsigned long *) = NULL;
	if (!real)
		real = (Bool(*)(XFontStruct *, Atom, unsigned long *))X11_FN("XGetFontProperty");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(fs, atom, value) : 0;
	xlog_ts(&ts, "XGetFontProperty(font_struct=%p,atom=0x%lx) -> %d value=0x%lx",
		(void *)fs, (unsigned long)atom, (int)r, value ? *value : 0);
	return r;
}

/* ------------------------------------------------------------------ */
/* keysyms / keyboard                                                  */
/* ------------------------------------------------------------------ */

KeySym *XGetKeyboardMapping(Display *d, KeyCode first, int count, int *per_key)
{
	static KeySym *(*real)(Display *, KeyCode, int, int *) = NULL;
	if (!real)
		real = (KeySym *(*)(Display *, KeyCode, int, int *))X11_FN("XGetKeyboardMapping");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	KeySym *r = real ? real(d, first, count, per_key) : NULL;
	xlog_ts(&ts, "XGetKeyboardMapping(d=%p,first_keycode=%u,keycode_count=%d) -> %p keysyms_per_keycode=%d",
		(void *)d, (unsigned int)first, count, (void *)r, per_key ? *per_key : -1);
	return r;
}

int XQueryKeymap(Display *d, char keys[32])
{
	static int (*real)(Display *, char[32]) = NULL;
	if (!real)
		real = (int(*)(Display *, char[32]))X11_FN("XQueryKeymap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, keys) : -1;
	xlog_ts(&ts, "XQueryKeymap(d=%p) -> %d keys=[%02x %02x %02x %02x ...]",
		(void *)d, r, (unsigned char)keys[0], (unsigned char)keys[1],
		(unsigned char)keys[2], (unsigned char)keys[3]);
	return r;
}

KeySym XStringToKeysym(const char *str)
{
	static KeySym (*real)(const char *) = NULL;
	if (!real)
		real = (KeySym(*)(const char *))X11_FN("XStringToKeysym");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	KeySym r = real ? real(str) : NoSymbol;
	xlog_ts(&ts, "XStringToKeysym(string=%s) -> 0x%lx", qstr(str, -1), (unsigned long)r);
	return r;
}

KeyCode XKeysymToKeycode(Display *d, KeySym ks)
{
	static KeyCode (*real)(Display *, KeySym) = NULL;
	if (!real)
		real = (KeyCode(*)(Display *, KeySym))X11_FN("XKeysymToKeycode");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	KeyCode r = real ? real(d, ks) : 0;
	xlog_ts(&ts, "XKeysymToKeycode(d=%p,keysym=0x%lx) -> %u", (void *)d, (unsigned long)ks, (unsigned int)r);
	return r;
}

/* ------------------------------------------------------------------ */
/* misc                                                                */
/* ------------------------------------------------------------------ */

int XFree(void *data)
{
	static int (*real)(void *) = NULL;
	if (!real)
		real = (int(*)(void *))X11_FN("XFree");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(data) : -1;
	xlog_ts(&ts, "XFree(data=%p) -> %d", data, r);
	return r;
}

Bool XQueryExtension(Display *d, const char *name, int *major, int *event, int *error)
{
	static Bool (*real)(Display *, const char *, int *, int *, int *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, const char *, int *, int *, int *))X11_FN("XQueryExtension");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, name, major, event, error) : 0;
	xlog_ts(&ts, "XQueryExtension(d=%p,name=%s) -> %d opcode=%d event=%d error=%d",
		(void *)d, qstr(name, -1), (int)r, major ? *major : -1,
		event ? *event : -1, error ? *error : -1);
	return r;
}

Bool XkbSetDetectableAutoRepeat(Display *d, Bool set, Bool *supported)
{
	static Bool (*real)(Display *, Bool, Bool *) = NULL;
	if (!real)
		real = (Bool(*)(Display *, Bool, Bool *))X11_FN("XkbSetDetectableAutoRepeat");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, set, supported) : 0;
	xlog_ts(&ts, "XkbSetDetectableAutoRepeat(d=%p,set_detectable=%d) -> %d supported=%d",
		(void *)d, (int)set, (int)r, supported ? (int)*supported : -1);
	return r;
}

/* ================================================================== */
/* second batch: the remaining Xlib symbols referenced by Tcl/Tk      */
/* (XIM, regions, fontsets, misc protocol helpers)                    */
/* ================================================================== */

XClassHint *XAllocClassHint(void)
{
	static XClassHint *(*real)(void) = NULL;
	if (!real)
		real = (XClassHint *(*)(void))X11_FN("XAllocClassHint");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XClassHint *r = real ? real() : NULL;
	xlog_ts(&ts, "XAllocClassHint() -> %p", (void *)r);
	return r;
}

Status XAllocNamedColor(Display *d, Colormap cmap, const char *name,
			XColor *screen, XColor *exact)
{
	static Status (*real)(Display *, Colormap, const char *, XColor *, XColor *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Colormap, const char *, XColor *, XColor *))X11_FN("XAllocNamedColor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, cmap, name, screen, exact) : 0;
	xlog_ts(&ts, "XAllocNamedColor(d=%p,cmap=0x%lx,color_name=%s) -> %d screen={pixel=0x%lx} exact={pixel=0x%lx}",
		(void *)d, (unsigned long)cmap, qstr(name, -1), (int)r,
		screen ? (unsigned long)screen->pixel : 0, exact ? (unsigned long)exact->pixel : 0);
	return r;
}

XSizeHints *XAllocSizeHints(void)
{
	static XSizeHints *(*real)(void) = NULL;
	if (!real)
		real = (XSizeHints *(*)(void))X11_FN("XAllocSizeHints");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XSizeHints *r = real ? real() : NULL;
	xlog_ts(&ts, "XAllocSizeHints() -> %p", (void *)r);
	return r;
}

int XBell(Display *d, int percent)
{
	static int (*real)(Display *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, int))X11_FN("XBell");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, percent) : -1;
	xlog_ts(&ts, "XBell(d=%p,percent=%d) -> %d", (void *)d, percent, r);
	return r;
}

int XClipBox(Region r, XRectangle *rect)
{
	static int (*real)(Region, XRectangle *) = NULL;
	if (!real)
		real = (int(*)(Region, XRectangle *))X11_FN("XClipBox");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r2 = real ? real(r, rect) : -1;
	xlog_ts(&ts, "XClipBox(r=%p) -> %d rect={x=%d,y=%d,w=%d,h=%d}",
		(void *)r, r2, rect ? rect->x : -1, rect ? rect->y : -1,
		rect ? rect->width : -1, rect ? rect->height : -1);
	return r2;
}

Status XCloseIM(XIM im)
{
	static Status (*real)(XIM) = NULL;
	if (!real)
		real = (Status(*)(XIM))X11_FN("XCloseIM");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(im) : 0;
	xlog_ts(&ts, "XCloseIM(im=%p) -> %d", (void *)im, (int)r);
	return r;
}

int XConvertSelection(Display *d, Atom selection, Atom target, Atom prop,
		      Window requestor, Time time)
{
	static int (*real)(Display *, Atom, Atom, Atom, Window, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Atom, Atom, Atom, Window, Time))X11_FN("XConvertSelection");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, selection, target, prop, requestor, time) : -1;
	xlog_ts(&ts, "XConvertSelection(d=%p,selection=%s,target=%s,property=%s,requestor=0x%lx,time=%lu) -> %d",
		(void *)d, atom_name(d, selection), atom_name(d, target), atom_name(d, prop),
		(unsigned long)requestor, (unsigned long)time, r);
	return r;
}

Pixmap XCreateBitmapFromData(Display *d, Drawable dw, const char *data,
			     unsigned int w, unsigned int h)
{
	static Pixmap (*real)(Display *, Drawable, const char *, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (Pixmap(*)(Display *, Drawable, const char *, unsigned int, unsigned int))X11_FN("XCreateBitmapFromData");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Pixmap r = real ? real(d, dw, data, w, h) : 0;
	xlog_ts(&ts, "XCreateBitmapFromData(d=%p,drawable=0x%lx,data=%p,w=%u,h=%u) -> 0x%lx",
		(void *)d, (unsigned long)dw, (const void *)data, w, h, (unsigned long)r);
	return r;
}

XFontSet XCreateFontSet(Display *d, const char *base, char ***missing,
			int *nmissing, char **defstr)
{
	static XFontSet (*real)(Display *, const char *, char ***, int *, char **) = NULL;
	if (!real)
		real = (XFontSet(*)(Display *, const char *, char ***, int *, char **))X11_FN("XCreateFontSet");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XFontSet r = real ? real(d, base, missing, nmissing, defstr) : NULL;
	xlog_ts(&ts, "XCreateFontSet(d=%p,base_font_name_list=%s) -> %p missing_charset_count=%d",
		(void *)d, qstr(base, -1), (void *)r, nmissing ? *nmissing : -1);
	return r;
}

Cursor XCreateGlyphCursor(Display *d, Font src, Font mask, unsigned int sc,
			  unsigned int mc, const XColor *fg, const XColor *bg)
{
	static Cursor (*real)(Display *, Font, Font, unsigned int, unsigned int,
			      const XColor *, const XColor *) = NULL;
	if (!real)
		real = (Cursor(*)(Display *, Font, Font, unsigned int, unsigned int,
				 const XColor *, const XColor *))X11_FN("XCreateGlyphCursor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Cursor r = real ? real(d, src, mask, sc, mc, fg, bg) : 0;
	xlog_ts(&ts, "XCreateGlyphCursor(d=%p,source_font=0x%lx,mask_font=0x%lx,source_char=%u,mask_char=%u,fg=%p,bg=%p) -> 0x%lx",
		(void *)d, (unsigned long)src, (unsigned long)mask, sc, mc, (void *)fg, (void *)bg, (unsigned long)r);
	return r;
}

/* ------------------------------------------------------------------ */
/* varargs forwarding for the XIM varargs functions                    */
/*                                                                     */
/* libX11 is not built with -Bsymbolic on Alpine, so calls made        */
/* INTERNALLY by libX11 (e.g. XOpenIM -> XGetIMValues) also resolve    */
/* through the dynamic symbol table and hit our interposers. Truncating*/
/* a varargs list to (first, NULL) makes libX11 dereference garbage,   */
/* so the captured list must be forwarded VERBATIM. On i386 every var- */
/* arg slot is a pointer-sized stack word, so reading them as void*    */
/* and re-passing them as void* preserves the call faithfully.         */
/* ------------------------------------------------------------------ */

#define MAX_VA 64

typedef void *(*va_fn_t)();

/* XIM varargs are (attribute, value, ...) pairs terminated by a NULL
 * ATTRIBUTE, i.e. a NULL at an even 0-based position. A NULL can legitimately
 * appear as a VALUE (odd position), so only terminate on an even position. */
static int capture_va(void **out, va_list ap)
{
	int n = 0;
	while (n < MAX_VA) {
		void *v = va_arg(ap, void *);
		out[n++] = v;
		if ((n % 2 == 1) && v == NULL)
			break;
	}
	/* guarantee a NULL at an even (attribute) position */
	while (n % 2 == 0)
		out[n++] = NULL;
	return n;
}

static void *va_call(va_fn_t fn, void *f0, void **a, int n)
{
	switch (n) {
	case 0: return fn(f0);
	case 1: return fn(f0, a[0]);
	case 2: return fn(f0, a[0], a[1]);
	case 3: return fn(f0, a[0], a[1], a[2]);
	case 4: return fn(f0, a[0], a[1], a[2], a[3]);
	case 5: return fn(f0, a[0], a[1], a[2], a[3], a[4]);
	case 6: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5]);
	case 7: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6]);
	case 8: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]);
	case 9: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8]);
	case 10: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9]);
	case 11: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10]);
	case 12: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11]);
	case 13: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12]);
	case 14: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13]);
	case 15: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14]);
	case 16: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15]);
	case 17: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15], a[16]);
	case 18: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15], a[16], a[17]);
	case 19: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15], a[16], a[17], a[18]);
	case 20: return fn(f0, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15], a[16], a[17], a[18], a[19]);
	default: return NULL;
	}
}

XIC XCreateIC(XIM im, ...)
{
	static XIC (*real)(XIM, ...) = NULL;
	if (!real)
		real = (XIC(*)(XIM, ...))X11_FN("XCreateIC");
	va_list ap;
	void *va[MAX_VA];
	va_start(ap, im);
	int n = capture_va(va, ap);
	va_end(ap);
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XIC r = (XIC)va_call((va_fn_t)real, (void *)im, va, n);
	xlog_ts(&ts, "XCreateIC(im=%p, ...%d va words...) -> %p", (void *)im, n - 1, (void *)r);
	return r;
}

XImage *XCreateImage(Display *d, Visual *vis, unsigned int depth, int format,
		     int offset, char *data, unsigned int w, unsigned int h,
		     int pad, int bpl)
{
	static XImage *(*real)(Display *, Visual *, unsigned int, int, int, char *,
			       unsigned int, unsigned int, int, int) = NULL;
	if (!real)
		real = (XImage *(*)(Display *, Visual *, unsigned int, int, int, char *,
				    unsigned int, unsigned int, int, int))X11_FN("XCreateImage");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XImage *r = real ? real(d, vis, depth, format, offset, data, w, h, pad, bpl) : NULL;
	xlog_ts(&ts, "XCreateImage(d=%p,visual=%p,depth=%u,format=%d,offset=%d,data=%p,w=%u,h=%u,bitmap_pad=%d,bytes_per_line=%d) -> %p",
		(void *)d, (void *)vis, depth, format, offset, data, w, h, pad, bpl, (void *)r);
	return r;
}

Region XCreateRegion(void)
{
	static Region (*real)(void) = NULL;
	if (!real)
		real = (Region(*)(void))X11_FN("XCreateRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Region r = real ? real() : NULL;
	xlog_ts(&ts, "XCreateRegion() -> %p", (void *)r);
	return r;
}

void XDestroyIC(XIC ic)
{
	static void (*real)(XIC) = NULL;
	if (!real)
		real = (void(*)(XIC))X11_FN("XDestroyIC");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(ic);
	xlog_ts(&ts, "XDestroyIC(ic=%p)", (void *)ic);
}

int XDestroyRegion(Region r)
{
	static int (*real)(Region) = NULL;
	if (!real)
		real = (int(*)(Region))X11_FN("XDestroyRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r2 = real ? real(r) : -1;
	xlog_ts(&ts, "XDestroyRegion(r=%p) -> %d", (void *)r, r2);
	return r2;
}

Status XDisplayKeycodes(Display *d, int *min, int *max)
{
	static Status (*real)(Display *, int *, int *) = NULL;
	if (!real)
		real = (Status(*)(Display *, int *, int *))X11_FN("XDisplayKeycodes");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, min, max) : 0;
	xlog_ts(&ts, "XDisplayKeycodes(d=%p) -> %d min_keycode=%d max_keycode=%d",
		(void *)d, (int)r, min ? *min : -1, max ? *max : -1);
	return r;
}

int XEmptyRegion(Region r)
{
	static int (*real)(Region) = NULL;
	if (!real)
		real = (int(*)(Region))X11_FN("XEmptyRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r2 = real ? real(r) : -1;
	xlog_ts(&ts, "XEmptyRegion(r=%p) -> %d", (void *)r, r2);
	return r2;
}

Bool XFilterEvent(XEvent *ev, Window w)
{
	static Bool (*real)(XEvent *, Window) = NULL;
	if (!real)
		real = (Bool(*)(XEvent *, Window))X11_FN("XFilterEvent");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(ev, w) : 0;
	xlog_ts(&ts, "XFilterEvent(event=%p{type=%d(%s)},window=0x%lx) -> %d",
		(void *)ev, ev ? ev->type : -1, ev ? event_name(ev->type) : "?", (unsigned long)w, (int)r);
	return r;
}

int XForceScreenSaver(Display *d, int mode)
{
	static int (*real)(Display *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, int))X11_FN("XForceScreenSaver");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, mode) : -1;
	xlog_ts(&ts, "XForceScreenSaver(d=%p,mode=%d) -> %d", (void *)d, mode, r);
	return r;
}

void XFreeFontSet(Display *d, XFontSet fs)
{
	static void (*real)(Display *, XFontSet) = NULL;
	if (!real)
		real = (void(*)(Display *, XFontSet))X11_FN("XFreeFontSet");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(d, fs);
	xlog_ts(&ts, "XFreeFontSet(d=%p,font_set=%p)", (void *)d, (void *)fs);
}

int XFreeModifiermap(XModifierKeymap *map)
{
	static int (*real)(XModifierKeymap *) = NULL;
	if (!real)
		real = (int(*)(XModifierKeymap *))X11_FN("XFreeModifiermap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(map) : -1;
	xlog_ts(&ts, "XFreeModifiermap(modmap=%p) -> %d", (void *)map, r);
	return r;
}

void XFreeStringList(char **list)
{
	static void (*real)(char **) = NULL;
	if (!real)
		real = (void(*)(char **))X11_FN("XFreeStringList");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(list);
	xlog_ts(&ts, "XFreeStringList(list=%p)", (void *)list);
}

char *XGetAtomName(Display *d, Atom a)
{
	static char *(*real)(Display *, Atom) = NULL;
	if (!real)
		real = (char *(*)(Display *, Atom))X11_FN("XGetAtomName");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = real ? real(d, a) : NULL;
	xlog_ts(&ts, "XGetAtomName(d=%p,atom=0x%lx) -> %s", (void *)d, (unsigned long)a,
		r ? qstr(r, -1) : "(null)");
	return r;
}

Status XGetGCValues(Display *d, GC gc, unsigned long mask, XGCValues *values)
{
	static Status (*real)(Display *, GC, unsigned long, XGCValues *) = NULL;
	if (!real)
		real = (Status(*)(Display *, GC, unsigned long, XGCValues *))X11_FN("XGetGCValues");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, gc, mask, values) : 0;
	xlog_ts(&ts, "XGetGCValues(d=%p,gc=%p,valuemask=0x%lx) -> %d values={foreground=0x%lx,background=0x%lx,font=0x%lx}",
		(void *)d, (void *)gc, mask, (int)r,
		values ? (unsigned long)values->foreground : 0,
		values ? (unsigned long)values->background : 0,
		values ? (unsigned long)values->font : 0);
	return r;
}

XImage *XGetImage(Display *d, Drawable dw, int x, int y, unsigned int w,
		  unsigned int h, unsigned long plane_mask, int format)
{
	static XImage *(*real)(Display *, Drawable, int, int, unsigned int, unsigned int,
			       unsigned long, int) = NULL;
	if (!real)
		real = (XImage *(*)(Display *, Drawable, int, int, unsigned int, unsigned int,
				    unsigned long, int))X11_FN("XGetImage");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XImage *r = real ? real(d, dw, x, y, w, h, plane_mask, format) : NULL;
	xlog_ts(&ts, "XGetImage(d=%p,drawable=0x%lx,x=%d,y=%d,w=%u,h=%u,plane_mask=0x%lx,format=%d) -> %p",
		(void *)d, (unsigned long)dw, x, y, w, h, plane_mask, format, (void *)r);
	return r;
}

char *XGetIMValues(XIM im, ...)
{
	static char *(*real)(XIM, ...) = NULL;
	if (!real)
		real = (char *(*)(XIM, ...))X11_FN("XGetIMValues");
	va_list ap;
	void *va[MAX_VA];
	va_start(ap, im);
	int n = capture_va(va, ap);
	va_end(ap);
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = (char *)va_call((va_fn_t)real, (void *)im, va, n);
	xlog_ts(&ts, "XGetIMValues(im=%p, ...%d va words...) -> %s", (void *)im, n - 1,
		r ? qstr(r, -1) : "(null)");
	return r;
}

XModifierKeymap *XGetModifierMapping(Display *d)
{
	static XModifierKeymap *(*real)(Display *) = NULL;
	if (!real)
		real = (XModifierKeymap *(*)(Display *))X11_FN("XGetModifierMapping");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XModifierKeymap *r = real ? real(d) : NULL;
	xlog_ts(&ts, "XGetModifierMapping(d=%p) -> %p{max_keypermod=%d}", (void *)d, (void *)r,
		r ? r->max_keypermod : -1);
	return r;
}

XVisualInfo *XGetVisualInfo(Display *d, long mask, XVisualInfo *templ, int *nitems)
{
	static XVisualInfo *(*real)(Display *, long, XVisualInfo *, int *) = NULL;
	if (!real)
		real = (XVisualInfo *(*)(Display *, long, XVisualInfo *, int *))X11_FN("XGetVisualInfo");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XVisualInfo *r = real ? real(d, mask, templ, nitems) : NULL;
	xlog_ts(&ts, "XGetVisualInfo(d=%p,vinfo_mask=0x%lx,template=%p) -> %p nitems=%d",
		(void *)d, mask, (void *)templ, (void *)r, nitems ? *nitems : -1);
	return r;
}

Status XGetWMColormapWindows(Display *d, Window w, Window **cwins, int *count)
{
	static Status (*real)(Display *, Window, Window **, int *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, Window **, int *))X11_FN("XGetWMColormapWindows");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, cwins, count) : 0;
	xlog_ts(&ts, "XGetWMColormapWindows(d=%p,w=0x%lx) -> %d count=%d",
		(void *)d, (unsigned long)w, (int)r, count ? *count : -1);
	return r;
}

int XGrabServer(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XGrabServer");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XGrabServer(d=%p) -> %d", (void *)d, r);
	return r;
}

Status XIconifyWindow(Display *d, Window w, int screen)
{
	static Status (*real)(Display *, Window, int) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, int))X11_FN("XIconifyWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, screen) : 0;
	xlog_ts(&ts, "XIconifyWindow(d=%p,w=0x%lx,screen_number=%d) -> %d",
		(void *)d, (unsigned long)w, screen, (int)r);
	return r;
}

int XIntersectRegion(Region a, Region b, Region out)
{
	static int (*real)(Region, Region, Region) = NULL;
	if (!real)
		real = (int(*)(Region, Region, Region))X11_FN("XIntersectRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(a, b, out) : -1;
	xlog_ts(&ts, "XIntersectRegion(sra=%p,srb=%p) -> %d", (void *)a, (void *)b, r);
	return r;
}

KeySym XKeycodeToKeysym(Display *d, KeyCode keycode, int index)
{
	static KeySym (*real)(Display *, KeyCode, int) = NULL;
	if (!real)
		real = (KeySym(*)(Display *, KeyCode, int))X11_FN("XKeycodeToKeysym");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	KeySym r = real ? real(d, keycode, index) : NoSymbol;
	xlog_ts(&ts, "XKeycodeToKeysym(d=%p,keycode=%u,index=%d) -> 0x%lx",
		(void *)d, (unsigned int)keycode, index, (unsigned long)r);
	return r;
}

KeySym XkbKeycodeToKeysym(Display *d, KeyCode keycode, int group, int level)
{
	static KeySym (*real)(Display *, KeyCode, int, int) = NULL;
	if (!real)
		real = (KeySym(*)(Display *, KeyCode, int, int))X11_FN("XkbKeycodeToKeysym");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	KeySym r = real ? real(d, keycode, group, level) : NoSymbol;
	xlog_ts(&ts, "XkbKeycodeToKeysym(d=%p,keycode=%u,group=%d,level=%d) -> 0x%lx",
		(void *)d, (unsigned int)keycode, group, level, (unsigned long)r);
	return r;
}

char *XKeysymToString(KeySym ks)
{
	static char *(*real)(KeySym) = NULL;
	if (!real)
		real = (char *(*)(KeySym))X11_FN("XKeysymToString");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = real ? real(ks) : NULL;
	xlog_ts(&ts, "XKeysymToString(keysym=0x%lx) -> %s", (unsigned long)ks,
		r ? qstr(r, -1) : "(null)");
	return r;
}

XHostAddress *XListHosts(Display *d, int *nhosts, Bool *state)
{
	static XHostAddress *(*real)(Display *, int *, Bool *) = NULL;
	if (!real)
		real = (XHostAddress *(*)(Display *, int *, Bool *))X11_FN("XListHosts");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XHostAddress *r = real ? real(d, nhosts, state) : NULL;
	xlog_ts(&ts, "XListHosts(d=%p) -> %p nhosts=%d state=%d",
		(void *)d, (void *)r, nhosts ? *nhosts : -1, state ? (int)*state : -1);
	return r;
}

KeySym XLookupKeysym(XKeyEvent *ke, int index)
{
	static KeySym (*real)(XKeyEvent *, int) = NULL;
	if (!real)
		real = (KeySym(*)(XKeyEvent *, int))X11_FN("XLookupKeysym");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	KeySym r = real ? real(ke, index) : NoSymbol;
	xlog_ts(&ts, "XLookupKeysym(key_event={type=%d(%s),keycode=%u,state=0x%x},index=%d) -> 0x%lx",
		ke ? ke->type : -1, ke ? event_name(ke->type) : "?",
		ke ? (unsigned int)ke->keycode : 0, ke ? (unsigned int)ke->state : 0,
		index, (unsigned long)r);
	return r;
}

int XLookupString(XKeyEvent *ke, char *buf, int len, KeySym *ksym, XComposeStatus *status)
{
	static int (*real)(XKeyEvent *, char *, int, KeySym *, XComposeStatus *) = NULL;
	if (!real)
		real = (int(*)(XKeyEvent *, char *, int, KeySym *, XComposeStatus *))X11_FN("XLookupString");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(ke, buf, len, ksym, status) : -1;
	xlog_ts(&ts, "XLookupString(key_event={type=%d(%s),keycode=%u,state=0x%x},bytes_buffer=%d) -> %d buffer=%s keysym=0x%lx",
		ke ? ke->type : -1, ke ? event_name(ke->type) : "?",
		ke ? (unsigned int)ke->keycode : 0, ke ? (unsigned int)ke->state : 0,
		len, r, buf ? qstr(buf, r) : "(null)", ksym ? (unsigned long)*ksym : 0);
	return r;
}

int XNoOp(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XNoOp");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XNoOp(d=%p) -> %d", (void *)d, r);
	return r;
}

XIM XOpenIM(Display *d, XrmDatabase rdb, char *res_name, char *res_class)
{
	static XIM (*real)(Display *, XrmDatabase, char *, char *) = NULL;
	if (!real)
		real = (XIM(*)(Display *, XrmDatabase, char *, char *))X11_FN("XOpenIM");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XIM r = real ? real(d, rdb, res_name, res_class) : NULL;
	xlog_ts(&ts, "XOpenIM(d=%p,rdb=%p,res_name=%s,res_class=%s) -> %p",
		(void *)d, (void *)rdb, res_name ? qstr(res_name, -1) : "(null)",
		res_class ? qstr(res_class, -1) : "(null)", (void *)r);
	return r;
}

int XQueryColor(Display *d, Colormap cmap, XColor *c)
{
	static int (*real)(Display *, Colormap, XColor *) = NULL;
	if (!real)
		real = (int(*)(Display *, Colormap, XColor *))X11_FN("XQueryColor");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, cmap, c) : -1;
	xlog_ts(&ts, "XQueryColor(d=%p,cmap=0x%lx,color={pixel=0x%lx}) -> %d {red=%u,green=%u,blue=%u}",
		(void *)d, (unsigned long)cmap, c ? (unsigned long)c->pixel : 0, r,
		c ? (unsigned int)c->red : 0, c ? (unsigned int)c->green : 0, c ? (unsigned int)c->blue : 0);
	return r;
}

int XQueryColors(Display *d, Colormap cmap, XColor *c, int n)
{
	static int (*real)(Display *, Colormap, XColor *, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Colormap, XColor *, int))X11_FN("XQueryColors");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, cmap, c, n) : -1;
	xlog_ts(&ts, "XQueryColors(d=%p,cmap=0x%lx,colors=%p,ncolors=%d) -> %d",
		(void *)d, (unsigned long)cmap, (void *)c, n, r);
	return r;
}

Status XReconfigureWMWindow(Display *d, Window w, int screen, unsigned int mask,
			    XWindowChanges *changes)
{
	static Status (*real)(Display *, Window, int, unsigned int, XWindowChanges *) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, int, unsigned int, XWindowChanges *))X11_FN("XReconfigureWMWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, screen, mask, changes) : 0;
	xlog_ts(&ts, "XReconfigureWMWindow(d=%p,w=0x%lx,screen_number=%d,mask=0x%x,changes=%p) -> %d",
		(void *)d, (unsigned long)w, screen, mask, (void *)changes, (int)r);
	return r;
}

int XRectInRegion(Region r, int x, int y, unsigned int w, unsigned int h)
{
	static int (*real)(Region, int, int, unsigned int, unsigned int) = NULL;
	if (!real)
		real = (int(*)(Region, int, int, unsigned int, unsigned int))X11_FN("XRectInRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r2 = real ? real(r, x, y, w, h) : -1;
	xlog_ts(&ts, "XRectInRegion(r=%p,x=%d,y=%d,w=%u,h=%u) -> %d", (void *)r, x, y, w, h, r2);
	return r2;
}

int XRefreshKeyboardMapping(XMappingEvent *ev)
{
	static int (*real)(XMappingEvent *) = NULL;
	if (!real)
		real = (int(*)(XMappingEvent *))X11_FN("XRefreshKeyboardMapping");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(ev) : -1;
	xlog_ts(&ts, "XRefreshKeyboardMapping(event={first_keycode=%d,count=%d}) -> %d",
		ev ? (int)ev->first_keycode : -1, ev ? (int)ev->count : -1, r);
	return r;
}

Bool XRegisterIMInstantiateCallback(Display *d, XrmDatabase rdb, char *rn, char *rc,
				    XIDProc cb, XPointer data)
{
	static Bool (*real)(Display *, XrmDatabase, char *, char *, XIDProc, XPointer) = NULL;
	if (!real)
		real = (Bool(*)(Display *, XrmDatabase, char *, char *, XIDProc, XPointer))X11_FN("XRegisterIMInstantiateCallback");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, rdb, rn, rc, cb, data) : 0;
	xlog_ts(&ts, "XRegisterIMInstantiateCallback(d=%p,rdb=%p,res_name=%s,res_class=%s,callback=%p,client_data=%p) -> %d",
		(void *)d, (void *)rdb, rn ? qstr(rn, -1) : "(null)", rc ? qstr(rc, -1) : "(null)",
		(void *)cb, (void *)data, (int)r);
	return r;
}

int XResetScreenSaver(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XResetScreenSaver");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XResetScreenSaver(d=%p) -> %d", (void *)d, r);
	return r;
}

Window XRootWindow(Display *d, int screen_number)
{
	static Window (*real)(Display *, int) = NULL;
	if (!real)
		real = (Window(*)(Display *, int))X11_FN("XRootWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Window r = real ? real(d, screen_number) : 0;
	xlog_ts(&ts, "XRootWindow(d=%p,screen_number=%d) -> 0x%lx", (void *)d, screen_number, (unsigned long)r);
	return r;
}

int XSetCommand(Display *d, Window w, char **argv, int argc)
{
	static int (*real)(Display *, Window, char **, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, char **, int))X11_FN("XSetCommand");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, argv, argc) : -1;
	xlog_ts(&ts, "XSetCommand(d=%p,w=0x%lx,argv=%p,argc=%d) -> %d",
		(void *)d, (unsigned long)w, (void *)argv, argc, r);
	return r;
}

void XSetICFocus(XIC ic)
{
	static void (*real)(XIC) = NULL;
	if (!real)
		real = (void(*)(XIC))X11_FN("XSetICFocus");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(ic);
	xlog_ts(&ts, "XSetICFocus(ic=%p)", (void *)ic);
}

char *XSetICValues(XIC ic, ...)
{
	static char *(*real)(XIC, ...) = NULL;
	if (!real)
		real = (char *(*)(XIC, ...))X11_FN("XSetICValues");
	va_list ap;
	void *va[MAX_VA];
	va_start(ap, ic);
	int n = capture_va(va, ap);
	va_end(ap);
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = (char *)va_call((va_fn_t)real, (void *)ic, va, n);
	xlog_ts(&ts, "XSetICValues(ic=%p, ...%d va words...) -> %s", (void *)ic, n - 1,
		r ? qstr(r, -1) : "(null)");
	return r;
}

char *XSetIMValues(XIM im, ...)
{
	static char *(*real)(XIM, ...) = NULL;
	if (!real)
		real = (char *(*)(XIM, ...))X11_FN("XSetIMValues");
	va_list ap;
	void *va[MAX_VA];
	va_start(ap, im);
	int n = capture_va(va, ap);
	va_end(ap);
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = (char *)va_call((va_fn_t)real, (void *)im, va, n);
	xlog_ts(&ts, "XSetIMValues(im=%p, ...%d va words...) -> %s", (void *)im, n - 1,
		r ? qstr(r, -1) : "(null)");
	return r;
}

int XSetRegion(Display *d, GC gc, Region r)
{
	static int (*real)(Display *, GC, Region) = NULL;
	if (!real)
		real = (int(*)(Display *, GC, Region))X11_FN("XSetRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r2 = real ? real(d, gc, r) : -1;
	xlog_ts(&ts, "XSetRegion(d=%p,gc=%p,r=%p) -> %d", (void *)d, (void *)gc, (void *)r, r2);
	return r2;
}

int XSetSelectionOwner(Display *d, Atom selection, Window owner, Time time)
{
	static int (*real)(Display *, Atom, Window, Time) = NULL;
	if (!real)
		real = (int(*)(Display *, Atom, Window, Time))X11_FN("XSetSelectionOwner");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, selection, owner, time) : -1;
	xlog_ts(&ts, "XSetSelectionOwner(d=%p,selection=%s,owner=0x%lx,time=%lu) -> %d",
		(void *)d, atom_name(d, selection), (unsigned long)owner, (unsigned long)time, r);
	return r;
}

void XSetWMClientMachine(Display *d, Window w, XTextProperty *prop)
{
	static void (*real)(Display *, Window, XTextProperty *) = NULL;
	if (!real)
		real = (void(*)(Display *, Window, XTextProperty *))X11_FN("XSetWMClientMachine");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	if (real)
		real(d, w, prop);
	xlog_ts(&ts, "XSetWMClientMachine(d=%p,w=0x%lx,text_prop=%p{value=%s})",
		(void *)d, (unsigned long)w, (void *)prop,
		(prop && prop->value) ? qstr((const char *)prop->value, (int)prop->nitems) : "(null)");
}

Status XSetWMColormapWindows(Display *d, Window w, Window *cwins, int count)
{
	static Status (*real)(Display *, Window, Window *, int) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, Window *, int))X11_FN("XSetWMColormapWindows");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, cwins, count) : 0;
	xlog_ts(&ts, "XSetWMColormapWindows(d=%p,w=0x%lx,colormap_windows=%p,count=%d) -> %d",
		(void *)d, (unsigned long)w, (void *)cwins, count, (int)r);
	return r;
}

int XSetWindowColormap(Display *d, Window w, Colormap cmap)
{
	static int (*real)(Display *, Window, Colormap) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Colormap))X11_FN("XSetWindowColormap");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, w, cmap) : -1;
	xlog_ts(&ts, "XSetWindowColormap(d=%p,w=0x%lx,cmap=0x%lx) -> %d",
		(void *)d, (unsigned long)w, (unsigned long)cmap, r);
	return r;
}

int XStringListToTextProperty(char **list, int count, XTextProperty *prop)
{
	static int (*real)(char **, int, XTextProperty *) = NULL;
	if (!real)
		real = (int(*)(char **, int, XTextProperty *))X11_FN("XStringListToTextProperty");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(list, count, prop) : -1;
	xlog_ts(&ts, "XStringListToTextProperty(list=%p,count=%d) -> %d prop={value=%s,encoding=0x%lx,format=%d,nitems=%lu}",
		(void *)list, count, r,
		(prop && prop->value) ? qstr((const char *)prop->value, (int)prop->nitems) : "(null)",
		prop ? (unsigned long)prop->encoding : 0, prop ? prop->format : -1,
		prop ? prop->nitems : 0);
	return r;
}

int XSubtractRegion(Region a, Region b, Region out)
{
	static int (*real)(Region, Region, Region) = NULL;
	if (!real)
		real = (int(*)(Region, Region, Region))X11_FN("XSubtractRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(a, b, out) : -1;
	xlog_ts(&ts, "XSubtractRegion(sra=%p,srb=%p) -> %d", (void *)a, (void *)b, r);
	return r;
}

typedef int (*XSyncHandlerFn)(Display *);

XSyncHandlerFn XSynchronize(Display *d, Bool onoff)
{
	static XSyncHandlerFn (*real)(Display *, Bool) = NULL;
	if (!real)
		real = (XSyncHandlerFn(*)(Display *, Bool))X11_FN("XSynchronize");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XSyncHandlerFn r = real ? real(d, onoff) : NULL;
	xlog_ts(&ts, "XSynchronize(d=%p,onoff=%d) -> %p", (void *)d, (int)onoff, (void *)r);
	return r;
}

int XUnionRectWithRegion(XRectangle *rect, Region src, Region out)
{
	static int (*real)(XRectangle *, Region, Region) = NULL;
	if (!real)
		real = (int(*)(XRectangle *, Region, Region))X11_FN("XUnionRectWithRegion");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(rect, src, out) : -1;
	xlog_ts(&ts, "XUnionRectWithRegion(rectangle={x=%d,y=%d,w=%d,h=%d}) -> %d",
		rect ? rect->x : -1, rect ? rect->y : -1,
		rect ? rect->width : -1, rect ? rect->height : -1, r);
	return r;
}

int XUngrabServer(Display *d)
{
	static int (*real)(Display *) = NULL;
	if (!real)
		real = (int(*)(Display *))X11_FN("XUngrabServer");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d) : -1;
	xlog_ts(&ts, "XUngrabServer(d=%p) -> %d", (void *)d, r);
	return r;
}

Bool XUnregisterIMInstantiateCallback(Display *d, XrmDatabase rdb, char *rn, char *rc,
				      XIDProc cb, XPointer data)
{
	static Bool (*real)(Display *, XrmDatabase, char *, char *, XIDProc, XPointer) = NULL;
	if (!real)
		real = (Bool(*)(Display *, XrmDatabase, char *, char *, XIDProc, XPointer))X11_FN("XUnregisterIMInstantiateCallback");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Bool r = real ? real(d, rdb, rn, rc, cb, data) : 0;
	xlog_ts(&ts, "XUnregisterIMInstantiateCallback(d=%p,rdb=%p,res_name=%s,res_class=%s,callback=%p,client_data=%p) -> %d",
		(void *)d, (void *)rdb, rn ? qstr(rn, -1) : "(null)", rc ? qstr(rc, -1) : "(null)",
		(void *)cb, (void *)data, (int)r);
	return r;
}

XVaNestedList XVaCreateNestedList(int dummy, ...)
{
	static XVaNestedList (*real)(int, ...) = NULL;
	if (!real)
		real = (XVaNestedList(*)(int, ...))X11_FN("XVaCreateNestedList");
	va_list ap;
	void *va[MAX_VA];
	va_start(ap, dummy);
	int n = capture_va(va, ap);
	va_end(ap);
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	XVaNestedList r = (XVaNestedList)va_call((va_fn_t)real, (void *)(long)dummy, va, n);
	xlog_ts(&ts, "XVaCreateNestedList(dummy=%d, ...%d va words...) -> %p", dummy, n - 1, (void *)r);
	return r;
}

char *XGetICValues(XIC ic, ...)
{
	static char *(*real)(XIC, ...) = NULL;
	if (!real)
		real = (char *(*)(XIC, ...))X11_FN("XGetICValues");
	va_list ap;
	void *va[MAX_VA];
	va_start(ap, ic);
	int n = capture_va(va, ap);
	va_end(ap);
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	char *r = (char *)va_call((va_fn_t)real, (void *)ic, va, n);
	xlog_ts(&ts, "XGetICValues(ic=%p, ...%d va words...) -> %s", (void *)ic, n - 1,
		r ? qstr(r, -1) : "(null)");
	return r;
}

VisualID XVisualIDFromVisual(Visual *vis)
{
	static VisualID (*real)(Visual *) = NULL;
	if (!real)
		real = (VisualID(*)(Visual *))X11_FN("XVisualIDFromVisual");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	VisualID r = real ? real(vis) : 0;
	xlog_ts(&ts, "XVisualIDFromVisual(visual=%p) -> 0x%lx", (void *)vis, (unsigned long)r);
	return r;
}

int XWarpPointer(Display *d, Window src, Window dst, int sx, int sy,
		 unsigned int sw, unsigned int sh, int dx, int dy)
{
	static int (*real)(Display *, Window, Window, int, int, unsigned int, unsigned int, int, int) = NULL;
	if (!real)
		real = (int(*)(Display *, Window, Window, int, int, unsigned int, unsigned int, int, int))X11_FN("XWarpPointer");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	int r = real ? real(d, src, dst, sx, sy, sw, sh, dx, dy) : -1;
	xlog_ts(&ts, "XWarpPointer(d=%p,src_w=0x%lx,dest_w=0x%lx,src_x=%d,src_y=%d,src_w=%u,src_h=%u,dest_x=%d,dest_y=%d) -> %d",
		(void *)d, (unsigned long)src, (unsigned long)dst, sx, sy, sw, sh, dx, dy, r);
	return r;
}

Status XWithdrawWindow(Display *d, Window w, int screen)
{
	static Status (*real)(Display *, Window, int) = NULL;
	if (!real)
		real = (Status(*)(Display *, Window, int))X11_FN("XWithdrawWindow");
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	ENTRY_LOG(&ts);
	Status r = real ? real(d, w, screen) : 0;
	xlog_ts(&ts, "XWithdrawWindow(d=%p,w=0x%lx,screen_number=%d) -> %d",
		(void *)d, (unsigned long)w, screen, (int)r);
	return r;
}
