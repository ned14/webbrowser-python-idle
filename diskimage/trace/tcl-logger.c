/*
 * tcl-logger.c - LD_PRELOAD shim that logs Tcl/Tk C entry points at ENTRY.
 *
 * Purpose: pinpoint the tk.Tk() hang in the CheerpX guest. The X11 entry
 * logger proved the hang is NOT inside any interposed Xlib call; the spin is
 * in Tcl/Tk C code, a Tcl script, or the runtime. This logger shows the
 * Tcl/Tk call path right up to the spin: a function that is entered but never
 * returns is the LAST line of the trace.
 *
 * Line format:  TCL<TAB><monotonic_sec.usec><TAB><func>(<args>) ENTERED
 * (CLOCK_MONOTONIC at entry; ENTRY-only - a hung call never logs a return).
 *
 * Real symbols resolve with dlopen("libtcl8.6.so")/("libtk8.6.so") + dlsym
 * (NEVER RTLD_NEXT - it fails inside the Tk process for non-libc symbols).
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -shared -fPIC -o tcl-logger.so tcl-logger.c -ldl
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <dlfcn.h>
#include <time.h>

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>

#include <tcl.h>
#include <tk.h>

/* tcl.h/tk.h define these convenience names as MACROS that expand to other
 * functions; the underlying symbols are the ones we interpose, so neutralise
 * the macro forms. */
#undef Tcl_Eval
#undef Tcl_GetVar
#undef Tcl_SetVar
#undef Tcl_GetIndexFromObj
#undef Tk_WindowId

/* ------------------------------------------------------------------ */
/* logging                                                             */
/* ------------------------------------------------------------------ */

static void xlog_ts(const struct timespec *ts, const char *fmt, ...)
{
	char buf[4096];
	va_list ap;
	int n = snprintf(buf, sizeof(buf), "TCL\t%ld.%06ld\t",
			 (long)ts->tv_sec, (long)ts->tv_nsec / 1000);
	if (n < 0 || n >= (int)sizeof(buf))
		return;
	va_start(ap, fmt);
	vsnprintf(buf + n, sizeof(buf) - n, fmt, ap);
	va_end(ap);
	/* single write() per line (no newline interleaving on the console) */
	{
		size_t m = strlen(buf);
		if (m + 1 < sizeof(buf)) {
			buf[m] = '\n';
			buf[m + 1] = 0;
		}
	}
	fputs(buf, stderr);
}

/* Timestamp without going through the PLT (not interposed here, but keep it
 * direct and cheap). */
static void mono_ts(struct timespec *ts)
{
	clock_gettime(CLOCK_MONOTONIC, ts);
}

#define ENTRY(fmt, ...) do { \
	struct timespec _ts; \
	mono_ts(&_ts); \
	xlog_ts(&_ts, "%s(" fmt ") ENTERED", __func__, __VA_ARGS__); \
} while (0)

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
/* real-symbol resolution                                              */
/* ------------------------------------------------------------------ */

static void *real_tcl(const char *name)
{
	static void *h = NULL;
	if (!h) {
		h = dlopen("libtcl8.6.so", RTLD_LAZY | RTLD_GLOBAL);
		if (!h)
			fprintf(stderr, "TCL\tlogger: dlopen(libtcl8.6.so): %s\n", dlerror());
	}
	void *p = h ? dlsym(h, name) : NULL;
	if (!p)
		fprintf(stderr, "TCL\tlogger: dlsym(%s): %s\n", name, dlerror());
	return p;
}

static void *real_tk(const char *name)
{
	static void *h = NULL;
	if (!h) {
		h = dlopen("libtk8.6.so", RTLD_LAZY | RTLD_GLOBAL);
		if (!h)
			fprintf(stderr, "TCL\tlogger: dlopen(libtk8.6.so): %s\n", dlerror());
	}
	void *p = h ? dlsym(h, name) : NULL;
	if (!p)
		fprintf(stderr, "TCL\tlogger: dlsym(%s): %s\n", name, dlerror());
	return p;
}

/* Tcl_GetString via the real pointer (not interposed here). */
static const char *objstr(Tcl_Obj *obj)
{
	static const char *(*real)(Tcl_Obj *) = NULL;
	if (!real)
		real = (const char *(*)(Tcl_Obj *))real_tcl("Tcl_GetString");
	return real && obj ? real(obj) : "(null)";
}

/* ------------------------------------------------------------------ */
/* Tcl                                                               */
/* ------------------------------------------------------------------ */

Tcl_Interp *Tcl_CreateInterp(void)
{
	static Tcl_Interp *(*real)(void) = NULL;
	if (!real)
		real = (Tcl_Interp *(*)(void))real_tcl("Tcl_CreateInterp");
	ENTRY("interp=%p", (void *)(real ? real() : NULL));
	return real ? real() : NULL;
}

int Tcl_Init(Tcl_Interp *interp)
{
	static int (*real)(Tcl_Interp *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *))real_tcl("Tcl_Init");
	ENTRY("interp=%p", (void *)interp);
	return real ? real(interp) : TCL_ERROR;
}

int Tcl_Eval(Tcl_Interp *interp, const char *script)
{
	static int (*real)(Tcl_Interp *, const char *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, const char *))real_tcl("Tcl_Eval");
	ENTRY("interp=%p script=%s", (void *)interp, qstr(script));
	return real ? real(interp, script) : TCL_ERROR;
}

int Tcl_EvalEx(Tcl_Interp *interp, const char *script, int numBytes, int flags)
{
	static int (*real)(Tcl_Interp *, const char *, int, int) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, const char *, int, int))real_tcl("Tcl_EvalEx");
	ENTRY("interp=%p script=%s numBytes=%d flags=0x%x", (void *)interp,
		qstr(script), numBytes, flags);
	return real ? real(interp, script, numBytes, flags) : TCL_ERROR;
}

int Tcl_EvalObjv(Tcl_Interp *interp, int objc, Tcl_Obj *const objv[], int flags)
{
	static int (*real)(Tcl_Interp *, int, Tcl_Obj *const[], int) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, int, Tcl_Obj *const[], int))real_tcl("Tcl_EvalObjv");
	ENTRY("interp=%p objc=%d cmd=%s flags=0x%x", (void *)interp, objc,
		(objc >= 1 && objv) ? qstr(objstr(objv[0])) : "(none)", flags);
	return real ? real(interp, objc, objv, flags) : TCL_ERROR;
}

int Tcl_EvalObjEx(Tcl_Interp *interp, Tcl_Obj *objPtr, int flags)
{
	static int (*real)(Tcl_Interp *, Tcl_Obj *, int) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, Tcl_Obj *, int))real_tcl("Tcl_EvalObjEx");
	ENTRY("interp=%p obj=%s flags=0x%x", (void *)interp, objstr(objPtr), flags);
	return real ? real(interp, objPtr, flags) : TCL_ERROR;
}

int Tcl_EvalFile(Tcl_Interp *interp, const char *fileName)
{
	static int (*real)(Tcl_Interp *, const char *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, const char *))real_tcl("Tcl_EvalFile");
	ENTRY("interp=%p fileName=%s", (void *)interp, qstr(fileName));
	return real ? real(interp, fileName) : TCL_ERROR;
}

const char *Tcl_PkgRequireEx(Tcl_Interp *interp, const char *name, const char *version,
			     int exact, void *clientDataPtr)
{
	static const char *(*real)(Tcl_Interp *, const char *, const char *, int, void *) = NULL;
	if (!real)
		real = (const char *(*)(Tcl_Interp *, const char *, const char *, int, void *))real_tcl("Tcl_PkgRequireEx");
	ENTRY("interp=%p name=%s version=%s exact=%d", (void *)interp,
		qstr(name), qstr(version), exact);
	return real ? real(interp, name, version, exact, clientDataPtr) : NULL;
}

Tcl_Command Tcl_FindCommand(Tcl_Interp *interp, const char *name,
			    Tcl_Namespace *contextNsPtr, int flags)
{
	static Tcl_Command (*real)(Tcl_Interp *, const char *, Tcl_Namespace *, int) = NULL;
	if (!real)
		real = (Tcl_Command(*)(Tcl_Interp *, const char *, Tcl_Namespace *, int))real_tcl("Tcl_FindCommand");
	ENTRY("interp=%p name=%s flags=0x%x", (void *)interp, qstr(name), flags);
	return real ? real(interp, name, contextNsPtr, flags) : NULL;
}

Tcl_Command Tcl_GetCommandFromObj(Tcl_Interp *interp, Tcl_Obj *objPtr)
{
	static Tcl_Command (*real)(Tcl_Interp *, Tcl_Obj *) = NULL;
	if (!real)
		real = (Tcl_Command(*)(Tcl_Interp *, Tcl_Obj *))real_tcl("Tcl_GetCommandFromObj");
	ENTRY("interp=%p obj=%s", (void *)interp, objstr(objPtr));
	return real ? real(interp, objPtr) : NULL;
}

Tcl_Command Tcl_CreateCommand(Tcl_Interp *interp, const char *cmdName,
			      Tcl_CmdProc *proc, ClientData clientData,
			      Tcl_CmdDeleteProc *deleteProc)
{
	static Tcl_Command (*real)(Tcl_Interp *, const char *, Tcl_CmdProc *, ClientData, Tcl_CmdDeleteProc *) = NULL;
	if (!real)
		real = (Tcl_Command(*)(Tcl_Interp *, const char *, Tcl_CmdProc *, ClientData, Tcl_CmdDeleteProc *))real_tcl("Tcl_CreateCommand");
	ENTRY("interp=%p cmdName=%s", (void *)interp, qstr(cmdName));
	return real ? real(interp, cmdName, proc, clientData, deleteProc) : NULL;
}

Tcl_Command Tcl_CreateObjCommand(Tcl_Interp *interp, const char *cmdName,
				 Tcl_ObjCmdProc *proc, ClientData clientData,
				 Tcl_CmdDeleteProc *deleteProc)
{
	static Tcl_Command (*real)(Tcl_Interp *, const char *, Tcl_ObjCmdProc *, ClientData, Tcl_CmdDeleteProc *) = NULL;
	if (!real)
		real = (Tcl_Command(*)(Tcl_Interp *, const char *, Tcl_ObjCmdProc *, ClientData, Tcl_CmdDeleteProc *))real_tcl("Tcl_CreateObjCommand");
	ENTRY("interp=%p cmdName=%s", (void *)interp, qstr(cmdName));
	return real ? real(interp, cmdName, proc, clientData, deleteProc) : NULL;
}

const char *Tcl_GetVar2(Tcl_Interp *interp, const char *part1, const char *part2, int flags)
{
	static const char *(*real)(Tcl_Interp *, const char *, const char *, int) = NULL;
	if (!real)
		real = (const char *(*)(Tcl_Interp *, const char *, const char *, int))real_tcl("Tcl_GetVar2");
	ENTRY("interp=%p part1=%s part2=%s flags=0x%x", (void *)interp,
		qstr(part1), qstr(part2), flags);
	return real ? real(interp, part1, part2, flags) : NULL;
}

const char *Tcl_SetVar2(Tcl_Interp *interp, const char *part1, const char *part2,
			const char *newValue, int flags)
{
	static const char *(*real)(Tcl_Interp *, const char *, const char *, const char *, int) = NULL;
	if (!real)
		real = (const char *(*)(Tcl_Interp *, const char *, const char *, const char *, int))real_tcl("Tcl_SetVar2");
	ENTRY("interp=%p part1=%s part2=%s value=%s flags=0x%x", (void *)interp,
		qstr(part1), qstr(part2), qstr(newValue), flags);
	return real ? real(interp, part1, part2, newValue, flags) : NULL;
}

Tcl_Obj *Tcl_ObjGetVar2(Tcl_Interp *interp, Tcl_Obj *part1Ptr, Tcl_Obj *part2Ptr, int flags)
{
	static Tcl_Obj *(*real)(Tcl_Interp *, Tcl_Obj *, Tcl_Obj *, int) = NULL;
	if (!real)
		real = (Tcl_Obj *(*)(Tcl_Interp *, Tcl_Obj *, Tcl_Obj *, int))real_tcl("Tcl_ObjGetVar2");
	ENTRY("interp=%p part1=%s flags=0x%x", (void *)interp, objstr(part1Ptr), flags);
	return real ? real(interp, part1Ptr, part2Ptr, flags) : NULL;
}

Tcl_Obj *Tcl_ObjSetVar2(Tcl_Interp *interp, Tcl_Obj *part1Ptr, Tcl_Obj *part2Ptr,
			Tcl_Obj *newValuePtr, int flags)
{
	static Tcl_Obj *(*real)(Tcl_Interp *, Tcl_Obj *, Tcl_Obj *, Tcl_Obj *, int) = NULL;
	if (!real)
		real = (Tcl_Obj *(*)(Tcl_Interp *, Tcl_Obj *, Tcl_Obj *, Tcl_Obj *, int))real_tcl("Tcl_ObjSetVar2");
	ENTRY("interp=%p part1=%s value=%s flags=0x%x", (void *)interp,
		objstr(part1Ptr), objstr(newValuePtr), flags);
	return real ? real(interp, part1Ptr, part2Ptr, newValuePtr, flags) : NULL;
}

const char *Tcl_GetStringResult(Tcl_Interp *interp)
{
	static const char *(*real)(Tcl_Interp *) = NULL;
	if (!real)
		real = (const char *(*)(Tcl_Interp *))real_tcl("Tcl_GetStringResult");
	ENTRY("interp=%p", (void *)interp);
	return real ? real(interp) : NULL;
}

Tcl_Obj *Tcl_GetObjResult(Tcl_Interp *interp)
{
	static Tcl_Obj *(*real)(Tcl_Interp *) = NULL;
	if (!real)
		real = (Tcl_Obj *(*)(Tcl_Interp *))real_tcl("Tcl_GetObjResult");
	ENTRY("interp=%p", (void *)interp);
	return real ? real(interp) : NULL;
}

int Tcl_DoOneEvent(int flags)
{
	static int (*real)(int) = NULL;
	if (!real)
		real = (int(*)(int))real_tcl("Tcl_DoOneEvent");
	ENTRY("flags=0x%x", flags);
	return real ? real(flags) : 0;
}

void Tcl_GetTime(Tcl_Time *timePtr)
{
	static void (*real)(Tcl_Time *) = NULL;
	if (!real)
		real = (void(*)(Tcl_Time *))real_tcl("Tcl_GetTime");
	ENTRY("timePtr=%p", (void *)timePtr);
	if (real)
		real(timePtr);
}

Tcl_Channel Tcl_FSOpenFileChannel(Tcl_Interp *interp, Tcl_Obj *pathPtr,
				  const char *modeString, int permissions)
{
	static Tcl_Channel (*real)(Tcl_Interp *, Tcl_Obj *, const char *, int) = NULL;
	if (!real)
		real = (Tcl_Channel(*)(Tcl_Interp *, Tcl_Obj *, const char *, int))real_tcl("Tcl_FSOpenFileChannel");
	ENTRY("interp=%p path=%s mode=%s", (void *)interp, objstr(pathPtr), qstr(modeString));
	return real ? real(interp, pathPtr, modeString, permissions) : NULL;
}

Tcl_Channel Tcl_OpenFileChannel(Tcl_Interp *interp, const char *fileName,
				const char *modeString, int permissions)
{
	static Tcl_Channel (*real)(Tcl_Interp *, const char *, const char *, int) = NULL;
	if (!real)
		real = (Tcl_Channel(*)(Tcl_Interp *, const char *, const char *, int))real_tcl("Tcl_OpenFileChannel");
	ENTRY("interp=%p fileName=%s mode=%s", (void *)interp, qstr(fileName), qstr(modeString));
	return real ? real(interp, fileName, modeString, permissions) : NULL;
}

int Tcl_ReadChars(Tcl_Channel chan, Tcl_Obj *objPtr, int toRead, int appendFlag)
{
	static int (*real)(Tcl_Channel, Tcl_Obj *, int, int) = NULL;
	if (!real)
		real = (int(*)(Tcl_Channel, Tcl_Obj *, int, int))real_tcl("Tcl_ReadChars");
	ENTRY("chan=%p toRead=%d append=%d", (void *)chan, toRead, appendFlag);
	return real ? real(chan, objPtr, toRead, appendFlag) : -1;
}

int Tcl_Close(Tcl_Interp *interp, Tcl_Channel chan)
{
	static int (*real)(Tcl_Interp *, Tcl_Channel) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, Tcl_Channel))real_tcl("Tcl_Close");
	ENTRY("interp=%p chan=%p", (void *)interp, (void *)chan);
	return real ? real(interp, chan) : TCL_ERROR;
}

char *Tcl_GetCwd(Tcl_Interp *interp, Tcl_DString *cwdPtr)
{
	static char *(*real)(Tcl_Interp *, Tcl_DString *) = NULL;
	if (!real)
		real = (char *(*)(Tcl_Interp *, Tcl_DString *))real_tcl("Tcl_GetCwd");
	ENTRY("interp=%p cwdPtr=%p", (void *)interp, (void *)cwdPtr);
	return real ? real(interp, cwdPtr) : NULL;
}

int Tcl_GetIndexFromObj(Tcl_Interp *interp, Tcl_Obj *objPtr,
			const char *const *tablePtr, const char *msg,
			int flags, int *indexPtr)
{
	static int (*real)(Tcl_Interp *, Tcl_Obj *, const char *const *, const char *, int, int *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, Tcl_Obj *, const char *const *, const char *, int, int *))real_tcl("Tcl_GetIndexFromObj");
	ENTRY("interp=%p obj=%s msg=%s", (void *)interp, objstr(objPtr), qstr(msg));
	return real ? real(interp, objPtr, tablePtr, msg, flags, indexPtr) : TCL_ERROR;
}

int Tcl_GetChannelOption(Tcl_Interp *interp, Tcl_Channel chan,
			 const char *optionName, Tcl_DString *dsPtr)
{
	static int (*real)(Tcl_Interp *, Tcl_Channel, const char *, Tcl_DString *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, Tcl_Channel, const char *, Tcl_DString *))real_tcl("Tcl_GetChannelOption");
	ENTRY("interp=%p chan=%p option=%s", (void *)interp, (void *)chan, qstr(optionName));
	return real ? real(interp, chan, optionName, dsPtr) : TCL_ERROR;
}

int Tcl_SetChannelOption(Tcl_Interp *interp, Tcl_Channel chan,
			 const char *optionName, const char *newValue)
{
	static int (*real)(Tcl_Interp *, Tcl_Channel, const char *, const char *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, Tcl_Channel, const char *, const char *))real_tcl("Tcl_SetChannelOption");
	ENTRY("interp=%p chan=%p option=%s value=%s", (void *)interp, (void *)chan,
		qstr(optionName), qstr(newValue));
	return real ? real(interp, chan, optionName, newValue) : TCL_ERROR;
}

void Tcl_BackgroundError(Tcl_Interp *interp)
{
	static void (*real)(Tcl_Interp *) = NULL;
	if (!real)
		real = (void(*)(Tcl_Interp *))real_tcl("Tcl_BackgroundError");
	ENTRY("interp=%p", (void *)interp);
	if (real)
		real(interp);
}

void Tcl_FindExecutable(const char *argv0)
{
	static void (*real)(const char *) = NULL;
	if (!real)
		real = (void(*)(const char *))real_tcl("Tcl_FindExecutable");
	ENTRY("argv0=%s", qstr(argv0));
	if (real)
		real(argv0);
}

/* ------------------------------------------------------------------ */
/* Tk                                                                */
/* ------------------------------------------------------------------ */

int Tk_Init(Tcl_Interp *interp)
{
	static int (*real)(Tcl_Interp *) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *))real_tk("Tk_Init");
	ENTRY("interp=%p", (void *)interp);
	return real ? real(interp) : TCL_ERROR;
}

Tk_Window Tk_CreateWindow(Tcl_Interp *interp, Tk_Window parent, const char *name,
			  const char *screenName)
{
	static Tk_Window (*real)(Tcl_Interp *, Tk_Window, const char *, const char *) = NULL;
	if (!real)
		real = (Tk_Window(*)(Tcl_Interp *, Tk_Window, const char *, const char *))real_tk("Tk_CreateWindow");
	ENTRY("interp=%p parent=%p name=%s", (void *)interp, (void *)parent, qstr(name));
	return real ? real(interp, parent, name, screenName) : NULL;
}

Tk_Uid Tk_GetOption(Tk_Window tkwin, const char *name, const char *className)
{
	static Tk_Uid (*real)(Tk_Window, const char *, const char *) = NULL;
	if (!real)
		real = (Tk_Uid(*)(Tk_Window, const char *, const char *))real_tk("Tk_GetOption");
	ENTRY("tkwin=%p name=%s class=%s", (void *)tkwin, qstr(name), qstr(className));
	return real ? real(tkwin, name, className) : NULL;
}

Tcl_Obj *Tk_GetOptionValue(Tcl_Interp *interp, char *recordPtr,
			   Tk_OptionTable optionTable, Tcl_Obj *namePtr, Tk_Window tkwin)
{
	static Tcl_Obj *(*real)(Tcl_Interp *, char *, Tk_OptionTable, Tcl_Obj *, Tk_Window) = NULL;
	if (!real)
		real = (Tcl_Obj *(*)(Tcl_Interp *, char *, Tk_OptionTable, Tcl_Obj *, Tk_Window))real_tk("Tk_GetOptionValue");
	ENTRY("interp=%p record=%p table=%p name=%s tkwin=%p", (void *)interp,
		(void *)recordPtr, (void *)optionTable, objstr(namePtr), (void *)tkwin);
	return real ? real(interp, recordPtr, optionTable, namePtr, tkwin) : NULL;
}

XColor *Tk_GetColor(Tcl_Interp *interp, Tk_Window tkwin, Tk_Uid name)
{
	static XColor *(*real)(Tcl_Interp *, Tk_Window, Tk_Uid) = NULL;
	if (!real)
		real = (XColor *(*)(Tcl_Interp *, Tk_Window, Tk_Uid))real_tk("Tk_GetColor");
	ENTRY("interp=%p tkwin=%p name=%s", (void *)interp, (void *)tkwin, qstr(name));
	return real ? real(interp, tkwin, name) : NULL;
}

XColor *Tk_GetColorFromObj(Tk_Window tkwin, Tcl_Obj *objPtr)
{
	static XColor *(*real)(Tk_Window, Tcl_Obj *) = NULL;
	if (!real)
		real = (XColor *(*)(Tk_Window, Tcl_Obj *))real_tk("Tk_GetColorFromObj");
	ENTRY("tkwin=%p obj=%s", (void *)tkwin, objstr(objPtr));
	return real ? real(tkwin, objPtr) : NULL;
}

GC Tk_GetGC(Tk_Window tkwin, unsigned long mask, XGCValues *values)
{
	static GC (*real)(Tk_Window, unsigned long, XGCValues *) = NULL;
	if (!real)
		real = (GC(*)(Tk_Window, unsigned long, XGCValues *))real_tk("Tk_GetGC");
	ENTRY("tkwin=%p mask=0x%lx", (void *)tkwin, mask);
	return real ? real(tkwin, mask, values) : NULL;
}

GC Tk_GetGCFromObj(Tcl_Interp *interp, Tk_Window tkwin, Tcl_Obj *objPtr)
{
	static GC (*real)(Tcl_Interp *, Tk_Window, Tcl_Obj *) = NULL;
	if (!real)
		real = (GC(*)(Tcl_Interp *, Tk_Window, Tcl_Obj *))real_tk("Tk_GetGCFromObj");
	ENTRY("interp=%p tkwin=%p obj=%s", (void *)interp, (void *)tkwin, objstr(objPtr));
	return real ? real(interp, tkwin, objPtr) : NULL;
}

Tk_Cursor Tk_GetCursor(Tcl_Interp *interp, Tk_Window tkwin, Tk_Uid name)
{
	static Tk_Cursor (*real)(Tcl_Interp *, Tk_Window, Tk_Uid) = NULL;
	if (!real)
		real = (Tk_Cursor(*)(Tcl_Interp *, Tk_Window, Tk_Uid))real_tk("Tk_GetCursor");
	ENTRY("interp=%p tkwin=%p name=%s", (void *)interp, (void *)tkwin, qstr(name));
	return real ? real(interp, tkwin, name) : NULL;
}

Tk_Cursor Tk_GetCursorFromObj(Tk_Window tkwin, Tcl_Obj *objPtr)
{
	static Tk_Cursor (*real)(Tk_Window, Tcl_Obj *) = NULL;
	if (!real)
		real = (Tk_Cursor(*)(Tk_Window, Tcl_Obj *))real_tk("Tk_GetCursorFromObj");
	ENTRY("tkwin=%p obj=%s", (void *)tkwin, objstr(objPtr));
	return real ? real(tkwin, objPtr) : NULL;
}

Tk_Font Tk_GetFont(Tcl_Interp *interp, Tk_Window tkwin, const char *name)
{
	static Tk_Font (*real)(Tcl_Interp *, Tk_Window, const char *) = NULL;
	if (!real)
		real = (Tk_Font(*)(Tcl_Interp *, Tk_Window, const char *))real_tk("Tk_GetFont");
	ENTRY("interp=%p tkwin=%p name=%s", (void *)interp, (void *)tkwin, qstr(name));
	return real ? real(interp, tkwin, name) : NULL;
}

Tk_Font Tk_GetFontFromObj(Tk_Window tkwin, Tcl_Obj *objPtr)
{
	static Tk_Font (*real)(Tk_Window, Tcl_Obj *) = NULL;
	if (!real)
		real = (Tk_Font(*)(Tk_Window, Tcl_Obj *))real_tk("Tk_GetFontFromObj");
	ENTRY("tkwin=%p obj=%s", (void *)tkwin, objstr(objPtr));
	return real ? real(tkwin, objPtr) : NULL;
}

Pixmap Tk_GetPixmap(Display *display, Drawable d, int width, int height, int depth)
{
	static Pixmap (*real)(Display *, Drawable, int, int, int) = NULL;
	if (!real)
		real = (Pixmap(*)(Display *, Drawable, int, int, int))real_tk("Tk_GetPixmap");
	ENTRY("display=%p w=%d h=%d depth=%d", (void *)display, width, height, depth);
	return real ? real(display, d, width, height, depth) : 0;
}

Tk_Uid Tk_GetUid(const char *str)
{
	static Tk_Uid (*real)(const char *) = NULL;
	if (!real)
		real = (Tk_Uid(*)(const char *))real_tk("Tk_GetUid");
	ENTRY("str=%s", qstr(str));
	return real ? real(str) : NULL;
}

const char *Tk_SetAppName(Tk_Window tkwin, const char *name)
{
	static const char *(*real)(Tk_Window, const char *) = NULL;
	if (!real)
		real = (const char *(*)(Tk_Window, const char *))real_tk("Tk_SetAppName");
	ENTRY("tkwin=%p name=%s", (void *)tkwin, qstr(name));
	return real ? real(tkwin, name) : NULL;
}

Tk_Window Tk_MainWindow(Tcl_Interp *interp)
{
	static Tk_Window (*real)(Tcl_Interp *) = NULL;
	if (!real)
		real = (Tk_Window(*)(Tcl_Interp *))real_tk("Tk_MainWindow");
	ENTRY("interp=%p", (void *)interp);
	return real ? real(interp) : NULL;
}

Window Tk_WindowId(Tk_Window tkwin)
{
	static Window (*real)(Tk_Window) = NULL;
	if (!real)
		real = (Window(*)(Tk_Window))real_tk("Tk_WindowId");
	ENTRY("tkwin=%p", (void *)tkwin);
	return real ? real(tkwin) : 0;
}

KeySym Tk_GetKeySym(Tk_Window tkwin, XEvent *eventPtr)
{
	static KeySym (*real)(Tk_Window, XEvent *) = NULL;
	if (!real)
		real = (KeySym(*)(Tk_Window, XEvent *))real_tk("Tk_GetKeySym");
	ENTRY("tkwin=%p event=%p", (void *)tkwin, (void *)eventPtr);
	return real ? real(tkwin, eventPtr) : NoSymbol;
}

Atom Tk_InternAtom(Tk_Window tkwin, const char *name)
{
	static Atom (*real)(Tk_Window, const char *) = NULL;
	if (!real)
		real = (Atom(*)(Tk_Window, const char *))real_tk("Tk_InternAtom");
	ENTRY("tkwin=%p name=%s", (void *)tkwin, qstr(name));
	return real ? real(tkwin, name) : 0;
}

void Tk_CreateEventHandler(Tk_Window token, unsigned long mask,
			   Tk_EventProc *proc, ClientData clientData)
{
	static void (*real)(Tk_Window, unsigned long, Tk_EventProc *, ClientData) = NULL;
	if (!real)
		real = (void(*)(Tk_Window, unsigned long, Tk_EventProc *, ClientData))real_tk("Tk_CreateEventHandler");
	ENTRY("tkwin=%p mask=0x%lx", (void *)token, mask);
	if (real)
		real(token, mask, proc, clientData);
}

void Tk_HandleEvent(XEvent *eventPtr)
{
	static void (*real)(XEvent *) = NULL;
	if (!real)
		real = (void(*)(XEvent *))real_tk("Tk_HandleEvent");
	ENTRY("event=%p type=%d", (void *)eventPtr, eventPtr ? eventPtr->type : -1);
	if (real)
		real(eventPtr);
}

void Tk_QueueWindowEvent(XEvent *eventPtr, Tcl_QueuePosition position)
{
	static void (*real)(XEvent *, Tcl_QueuePosition) = NULL;
	if (!real)
		real = (void(*)(XEvent *, Tcl_QueuePosition))real_tk("Tk_QueueWindowEvent");
	ENTRY("event=%p type=%d", (void *)eventPtr, eventPtr ? eventPtr->type : -1);
	if (real)
		real(eventPtr, position);
}

int Tk_InitOptions(Tcl_Interp *interp, char *recordPtr, Tk_OptionTable optionToken,
		   Tk_Window tkwin)
{
	static int (*real)(Tcl_Interp *, char *, Tk_OptionTable, Tk_Window) = NULL;
	if (!real)
		real = (int(*)(Tcl_Interp *, char *, Tk_OptionTable, Tk_Window))real_tk("Tk_InitOptions");
	ENTRY("interp=%p record=%p table=%p tkwin=%p", (void *)interp,
		(void *)recordPtr, (void *)optionToken, (void *)tkwin);
	return real ? real(interp, recordPtr, optionToken, tkwin) : TCL_ERROR;
}

/* TkpOpenDisplay is internal but exported by libtk8.6. */
typedef struct TkDisplay TkDisplay;
TkDisplay *TkpOpenDisplay(const char *displayNameStr)
{
	static TkDisplay *(*real)(const char *) = NULL;
	if (!real)
		real = (TkDisplay *(*)(const char *))real_tk("TkpOpenDisplay");
	ENTRY("displayNameStr=%s", qstr(displayNameStr));
	return real ? real(displayNameStr) : NULL;
}
