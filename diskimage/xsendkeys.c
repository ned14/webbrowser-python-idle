/* xsendkeys — persistent XTEST key-event injector for the WebVM paste lane.
 *
 * Reads commands from stdin (one per line):
 *   key <keysym>    press + release
 *   down <keysym>   press
 *   up <keysym>     release
 *   usleep <us>     pause
 *   sync            explicit round-trip
 *
 * XSync()s after EVERY command. This is REQUIRED: without a round-trip the
 * X server processes only the FIRST FakeInput and silently drops the rest
 * (verified under Xvfb 2026-08-28 — one KeyPress delivered, then silence).
 *
 * The driver is paste-typer.sh (it owns the CXCLIP framing, the ASCII
 * typability gate and the char -> keysym-name translation); this binary is
 * the minimal XTEST backend. Compiled in the Dockerfile `xsendkeys-build`
 * stage so no compiler ships in the final image. Deliberately NOT xdotool
 * (banned — AGENTS.md) and not libxdo; it links only libXtst + libX11.
 *
 * Build: gcc -O2 -o xsendkeys xsendkeys.c -lXtst -lX11
 */

#include <X11/Xlib.h>
#include <X11/extensions/XTest.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	(void)argc;
	(void)argv;
	const char *display = getenv("DISPLAY");
	if (!display || !*display)
		display = ":0";
	Display *dpy = XOpenDisplay(display);
	if (!dpy) {
		fprintf(stderr, "xsendkeys: cannot open display %s\n", display);
		return 1;
	}
	/* PASTE_DEBUG=1 (debug builds only): report the XTEST extension's
	 * presence/version to stderr (routed to the console by the typer).
	 * The emulated X server's XTEST support is NOT assumed — a missing or
	 * stubbed extension silently drops every FakeKeyEvent (2026-08-30). */
	if (getenv("PASTE_DEBUG")) {
		int ev, err, major = 0, minor = 0;
		if (XTestQueryExtension(dpy, &ev, &err, &major, &minor))
			fprintf(stderr, "xsendkeys: XTEST ok (events=%d errors=%d v%d.%d)\n",
				ev, err, major, minor);
		else
			fprintf(stderr, "xsendkeys: XTEST NOT AVAILABLE on %s\n", display);
	}
	/* The paste lane must not depend on the page's click having moved the X
	 * input focus: under the emulated X server click-to-focus is unreliable
	 * (ButtonPress reaches widgets, but the X input focus never follows —
	 * verified 2026-08-30). So the first key of every batch is preceded by
	 * an explicit XSetInputFocus on the first mapped top-level child of the
	 * root (the explorer). Tk routes the events to ITS focus widget (the
	 * Search entry, focus_force'd at startup), so the text lands even when
	 * the page's click never focused anything. XSetInputFocus is
	 * asynchronous (no reply round-trip) — safe under the emulated X. */
	{
		Window root, parent, *children = NULL;
		unsigned int nchildren = 0;
		Window target = 0;
		if (XQueryTree(dpy, DefaultRootWindow(dpy), &root, &parent, &children, &nchildren)) {
			for (unsigned int i = 0; i < nchildren && !target; i++) {
				XWindowAttributes attr;
				if (XGetWindowAttributes(dpy, children[i], &attr) && attr.map_state == IsViewable)
					target = children[i];
			}
			if (children)
				XFree(children);
		}
		if (target)
			XSetInputFocus(dpy, target, RevertToParent, CurrentTime);
	}
	char line[512];
	while (fgets(line, sizeof line, stdin)) {
		char cmd[32] = {0};
		char name[256] = {0};
		if (sscanf(line, "%31s %255s", cmd, name) < 1)
			continue;
		if (strcmp(cmd, "sync") == 0) {
			XSync(dpy, 0);
			continue;
		}
		if (strcmp(cmd, "usleep") == 0) {
			long us = atol(name);
			usleep(us > 0 ? (useconds_t)us : 10000);
			continue;
		}
		KeySym ks = XStringToKeysym(name);
		KeyCode kc = ks ? XKeysymToKeycode(dpy, ks) : 0;
		if (!kc) {
			fprintf(stderr, "xsendkeys: no keycode for %s\n", name);
			continue;
		}
		if (strcmp(cmd, "down") == 0) {
			XTestFakeKeyEvent(dpy, kc, True, 0);
		} else if (strcmp(cmd, "up") == 0) {
			XTestFakeKeyEvent(dpy, kc, False, 0);
		} else {
			/* "key": press + release */
			XTestFakeKeyEvent(dpy, kc, True, 0);
			XTestFakeKeyEvent(dpy, kc, False, 0);
		}
		XSync(dpy, 0); /* mandatory after every command */
	}
	XCloseDisplay(dpy);
	return 0;
}
