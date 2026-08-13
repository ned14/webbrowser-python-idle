#!/bin/sh
# CheerpX trace capture — autostarted by i3 on the X desktop (replaces the
# idle3.10 autostart while the Tk/CheerpX hang is being diagnosed).
#
# Mode is read from /trace/run-mode (baked at image build time):
#   syscall -> run ONLY the libc-interposer syscall logger
#   x11     -> run ONLY the Xlib-interposer X11 logger
#   both    -> run both IN PARALLEL (default)
#
# The tk.Tk() hang cannot be killed from inside the guest (it is unresponsive
# to signals AND starves other threads), so the page-side capture detects the
# console stall and tears the VM down. Parallel is the default so one boot
# yields both traces; the solo modes give clean single-trace runs (a parallel
# X11 run sees the other app's Tk registration as an existing "tk" interpreter,
# which shows up as a "#2" name suffix in the trace).
#
# The app's stdout is discarded; stderr (logger + trace marker) goes to
# /dev/console. Section markers frame each trace for the page-side capture.
set -u

export DISPLAY=:0
export HOME=/home/user
export LC_ALL=C
export PATH=/usr/bin:/bin:/sbin:/usr/sbin

MODE=$(cat /trace/run-mode 2>/dev/null || echo both)

echo "TRACE-RUN-START mode=$MODE" >/dev/console

	case "$MODE" in
	syscall)
		echo "===BEGIN-SYSCALL===" >/dev/console
		LD_PRELOAD=/trace/syscall-logger.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-SYSCALL-RC=$?" >/dev/console
		echo "===END-SYSCALL===" >/dev/console
		;;
	x11)
		echo "===BEGIN-X11CALLS===" >/dev/console
		LD_PRELOAD=/trace/xcall-logger.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-X11-RC=$?" >/dev/console
		echo "===END-X11CALLS===" >/dev/console
		;;
	x11-entry)
		echo "===BEGIN-X11CALLS===" >/dev/console
		LD_PRELOAD=/trace/xcall-logger-entry.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-X11-RC=$?" >/dev/console
		echo "===END-X11CALLS===" >/dev/console
		;;
	tcl)
		echo "===BEGIN-X11CALLS===" >/dev/console
		LD_PRELOAD=/trace/tcl-logger.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-X11-RC=$?" >/dev/console
		echo "===END-X11CALLS===" >/dev/console
		;;
	tclsh)
		echo "===BEGIN-X11CALLS===" >/dev/console
		LD_PRELOAD=/trace/tcl-logger.so /usr/bin/tclsh /trace/probe.tcl >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-X11-RC=$?" >/dev/console
		echo "===END-X11CALLS===" >/dev/console
		;;
	probe)
		# Direct-libc isatty/ioctl/getsockname probe (no Tcl/Tk/Python).
		# Runs under the syscall-logger so a skipped vs hanging syscall is
		# distinguishable: the probe's own PROBE\tENTER/RET lines bracket each
		# call; the interposer's SYS\t lines show whether the syscall was
		# actually issued. A call that prints ENTER but never RET is the hang.
		echo "===BEGIN-PROBE===" >/dev/console
		LD_PRELOAD=/trace/syscall-logger.so /trace/probe >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-PROBE-RC=$?" >/dev/console
		echo "===END-PROBE===" >/dev/console
		;;
	probe-plain)
		# Same probe WITHOUT the syscall-logger interposer: isolates the hang
		# from the logger (a call that prints ENTER but never RET hangs in the
		# guest libc/runtime itself).
		echo "===BEGIN-PROBE===" >/dev/console
		/trace/probe >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-PROBE-RC=$?" >/dev/console
		echo "===END-PROBE===" >/dev/console
		;;
	verify-tclsh)
		# Workaround verification: tclsh under getsockname-fix.so. Without the
		# shim this hangs inside getsockname() during channel init; with it the
		# probe script's puts "TCLSH-OK" must print and tclsh must exit 0.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so /usr/bin/tclsh /trace/probe.tcl >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TCLSH-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk)
		# Workaround verification: tk.Tk() under getsockname-fix.so. Without the
		# shim this hangs during Tcl channel init; with it example.py must reach
		# its TRACE_MAINLOOP_BEGIN marker (mainloop() then runs forever, so the
		# page-side capture bounds the run).
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-sys)
		# Workaround verification WITH the syscall-logger stacked on top of the
		# shim, so a residual hang's last syscall is visible (shim returns
		# ENOTSOCK for non-sockets; the logger records what the app actually
		# issues). Note: LD_PRELOAD order = shim first, logger second.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-x)
		# Deep diagnosis: getsockname shim + X11 ENTRY logger + syscall logger
		# all stacked, so the spinning code is identifiable at the X11 function
		# level (which X call is entered in a tight loop) AND at the syscall
		# level. Order: fix (wins getsockname for non-sockets), then the
		# loggers; they interpose disjoint symbol sets.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xcall-logger-entry.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-block)
		# Workaround combo: getsockname-fix + force the X socket into BLOCKING
		# mode (xblock-fix). Goal: tkinter reaches mainloop() (the
		# TRACE_MAINLOOP_BEGIN marker prints and mainloop keeps running).
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xblock-fix.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-block-sys)
		# verify-tk-block WITH the syscall-logger on top, to see whether the
		# XSync-style flush-wait still spins or now blocks.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xblock-fix.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-noxim)
		# XIM disable test: XOpenIM runs twice right before the §2.3 spin.
		# Set the Tk resource *useXIM: false (via xrdb) AND XMODIFIERS=@im=none
		# (Xlib reads it in XOpenIM). If the XSync storm was XIM-driven,
		# tkinter should now reach the TRACE_MAINLOOP_BEGIN marker.
		echo "===BEGIN-VERIFY===" >/dev/console
		DISPLAY=:0 xrdb -merge /trace/noxim.xresources >/dev/null 2>&1
		export XMODIFIERS=@im=none
		LD_PRELOAD=/trace/getsockname-fix.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-noxim-sys)
		# XIM disable + syscall-logger: does the storm disappear, and does the
		# app now proceed past it?
		echo "===BEGIN-VERIFY===" >/dev/console
		DISPLAY=:0 xrdb -merge /trace/noxim.xresources >/dev/null 2>&1
		export XMODIFIERS=@im=none
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-noxim-x)
		# XIM disable + X11 ENTRY logger: confirm XOpenIM is actually skipped
		# and identify what drives the residual storm at the X function level.
		echo "===BEGIN-VERIFY===" >/dev/console
		DISPLAY=:0 xrdb -merge /trace/noxim.xresources >/dev/null 2>&1
		export XMODIFIERS=@im=none
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xcall-logger-entry.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-tcl)
		# Stack ALL entry loggers + the shim on the Tk run: Tcl function level
		# (tcl-logger), X11 function level (xcall-logger-entry), syscall level
		# (syscall-logger). Their symbol sets are disjoint, so they coexist in
		# one LD_PRELOAD. The goal is to name the Tcl/Tk function whose
		# XSync-style flush-wait loops (§2.5) and pick the right short-circuit.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/tcl-logger.so:/trace/xcall-logger-entry.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-sync-sys)
		# Short-circuit workaround WITH the syscall logger: see whether the
		# storm is gone and where the app now goes (or stalls).
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xsync-fix.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-sync-x)
		# Short-circuit workaround + X11 ENTRY logger: confirm XNoOp/XSync are
		# actually suppressed and identify the residual looping X function.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xsync-fix.so:/trace/xcall-logger-entry.so:/trace/syscall-logger.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-tk-sync)
		# Short-circuit workaround: getsockname-fix + xsync-fix (XNoOp/XSync
		# become no-ops, XEventsQueued returns the local queue count without a
		# pending-reply block). window.update()'s Tcl_DoOneEvent should then
		# drain and the TRACE_MAINLOOP_BEGIN marker should print.
		echo "===BEGIN-VERIFY===" >/dev/console
		LD_PRELOAD=/trace/getsockname-fix.so:/trace/xsync-fix.so \
			/usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		wait "$!"
		echo "TRACE-VERIFY-TK-RC=$?" >/dev/console
		echo "===END-VERIFY===" >/dev/console
		;;
	verify-xterm)
		# Control: a REAL, WORKING X11 client (xterm) under the same enhanced
		# syscall-logger. xterm runs its own X socket poll/select loop; if
		# CheerpX's poll/select reported the X socket ready with no data, this
		# would busy-spin exactly like the Tk event loop. It is launched
		# non-interactively with a stub command so it stays alive until the
		# page-side capture stalls and tears the VM down. Compare its poll
		# readiness pattern to verify-tk-sys's.
		echo "===BEGIN-XTERM===" >/dev/console
		LD_PRELOAD=/trace/syscall-logger.so \
			xterm -e /bin/sh -c 'echo XTERM-OK >/dev/console; while :; do sleep 3600; done' \
			>/dev/null 2>/dev/console &
		XTERM_PID=$!
		wait "$XTERM_PID"
		echo "TRACE-XTERM-RC=$?" >/dev/console
		echo "===END-XTERM===" >/dev/console
		;;
	*)
		echo "===BEGIN-SYSCALL===" >/dev/console
		LD_PRELOAD=/trace/syscall-logger.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		SYS_PID=$!
		echo "===BEGIN-X11CALLS===" >/dev/console
		LD_PRELOAD=/trace/xcall-logger.so /usr/bin/python3 /trace/example.py >/dev/null 2>/dev/console &
		X11_PID=$!
		wait "$SYS_PID"
		echo "TRACE-SYSCALL-RC=$?" >/dev/console
		echo "===END-SYSCALL===" >/dev/console
		wait "$X11_PID"
		echo "TRACE-X11-RC=$?" >/dev/console
		echo "===END-X11CALLS===" >/dev/console
		;;
esac

echo "TRACE-RUN-END" >/dev/console
