# WebVM guest image: minimal i386 Alpine 3.24 with stdlib-only Python + IDLE
# (launched from the file explorer — a stdlib-only Tk app that autostarts at
# desktop boot and is replaced on screen by IDLE while IDLE runs),
# the Tk file viewer (images via py3-pillow, text/Markdown via py3-mistune),
# an Xorg/Openbox desktop, terminal, git tooling, and (for
# samba/webdav builds) the home-sync agent.
#
# STORAGE_BACKEND selects the sync agent:
#   browser/none -> no sync agent, no /root/.syncrc
#   samba        -> pysmb agent; /root/.syncrc targets the gateway tailnet IP
#   webdav       -> urllib agent; /root/.syncrc targets the gateway tailnet IP
# Runtime /opt/syncrc (injected by the page via a DataDevice) overrides the
# baked /root/.syncrc.
#
# Build args default to placeholders so CI and offline builds succeed; deploy-
# time values are passed by build.sh from .env.

FROM docker.io/i386/alpine:3.24

# CheerpX guest fix shims, built in a scratch stage (no compiler in the
# final image). faccessat-fix.so guards the *at() family against dfd=-1,
# which CheerpX's faccessat traps on and openrc 0.60+ calls by design
# (plans/update-to-latest.md Tier B §9.2; see diskimage/faccessat-fix.c).
FROM docker.io/i386/alpine:3.24 AS shimbuild
RUN apk add --no-cache gcc musl-dev
COPY faccessat-fix.c /tmp/faccessat-fix.c
RUN gcc -O2 -shared -fPIC -o /tmp/faccessat-fix.so /tmp/faccessat-fix.c

FROM docker.io/i386/alpine:3.24

ARG STORAGE_BACKEND=browser
ARG SYNC_URL=http://100.64.0.1:8082/webdav/
ARG SYNC_USER=webdav
ARG SYNC_PASS=changeme
ARG SAMBA_HOST=100.64.0.1
ARG SAMBA_SHARE=share
ARG SAMBA_USER=user
ARG SAMBA_PASS=changeme

# v3.24 (supported to 2028-06): rewrite BOTH repositories (don't append) so
# the community repo is enabled and the exact pins are used. dl-cdn.alpine-
# linux.org is the resolving mirror; if it ever stops serving v3.24, switch
# to https://archive.alpinelinux.org/alpine/v3.24/.
RUN cat > /etc/apk/repositories <<'EOF'
https://dl-cdn.alpinelinux.org/alpine/v3.24/main
https://dl-cdn.alpinelinux.org/alpine/v3.24/community
EOF

# CheerpX guest fix shims (Tier B): faccessat-fix.so guards the *at() family
# against dfd=-1, which CheerpX's faccessat traps on (openrc 0.60+ calls
# faccessat(-1, ...) on every service state check). It also implements
# sigprocmask(SIG_UNBLOCK) via the SIG_SETMASK path that works under
# CheerpX. It is loaded through LD_PRELOAD — set for the boot chain by
# rc-preload (inittab) and kept in the exec'd init scripts' env via
# rc_env_allow="LD_PRELOAD" (/etc/rc.conf, because openrc's env_filter()
# would otherwise scrub it — the exec'd openrc-run then crashes on
# faccessat(-1); diagnosed 2026-08-20). See diskimage/faccessat-fix.c.
COPY --from=shimbuild /tmp/faccessat-fix.so /usr/local/lib/faccessat-fix.so
# Desktop + tooling. python3-tkinter and the idlelib extraction come from
# community; everything else from main. xvfb is installed for the
# in-guest GUI test run (tests/rootfs/smoke.sh runs the file-explorer test
# suite under Xvfb) — it is not used by the desktop itself.
# py3-pillow + py3-mistune are the Tk file viewer's image/Markdown engines
# (see plans/future-feature-ideas.md): Pillow ships PIL.ImageTk + _imagingtk
# on this arch; both come from the v3.24 community repo, nothing pip.
# Versions (verified 2026-08-18, v3.24 x86): python3 3.14.7-r1,
# python3-tkinter 3.14.7-r0, py3-pillow 12.2.0-r0, py3-mistune 3.2.1-r0,
# tcl/tk 8.6.17-r1, xorg-server/xvfb 21.1.24-r0, openbox 3.6.1-r8,
# git 2.54.0-r0, openssh-client-default 10.3_p1-r0, busybox-extras
# 1.37.0-r31, dbus 1.16.2-r2, eudev 3.2.14-r6, font-dejavu 2.37-r6,
# xterm 410-r0, py3-pip 26.1.2-r0.
RUN apk add --no-cache \
	alpine-base \
	udev-init-scripts \
	udev-init-scripts-openrc \
	eudev \
	xorg-server \
	xvfb \
	xinit \
	xf86-input-libinput \
	xf86-input-evdev \
	xrandr \
	setxkbmap \
	openbox \
	xprop \
	xsetroot \
	font-dejavu \
	python3 \
	python3-tkinter \
	xterm \
	git \
	openssh-client-default \
	busybox-extras \
	dbus \
	py3-pip \
	py3-pillow \
	py3-mistune

# Mesa's GL stack (libGL/libEGL + libgallium + llvm22-libs, ~246 MiB) is the
# single biggest cost in the image and is NEVER used by this guest:
#   * Xorg runs with ShadowFB + AccelMethod none (pure software blits, see
#     10-cheerpx.conf) — no GLX, no glamor, no DRI.
#   * Tk/xterm/openbox render through X11 core requests, never GL.
#   * The only load-time GL references are Xvfb and the glx extension module,
#     which carry ~310 static gl*/glX* entry points into libGL but only ever
#     call them for actual GLX rendering — which no guest app does.
# So replace libGL.so.1 with a 176 KiB no-op stub exporting the same gl*/glX*
# symbol names, delete the rest of the mesa/llvm stack (and the GLX/glamor
# modules so Xorg never initializes GL), and keep libgbm + libdrm core (the
# modesetting driver links libgbm at load). Install-time apk deps are
# satisfied BEFORE the removal; the apk database afterwards is stale for the
# removed packages, which is harmless (nothing in the guest runs apk).
# Verified 2026-08-21: Xvfb, the in-guest file-explorer and file-viewer suites
# (PASS ALL), real viewer/IDLE launches and the openbox keep-alive all work
# with the stub; /usr/lib drops from 365.6 MiB to 119.3 MiB.
RUN apk add --no-cache gcc musl-dev binutils && \
	nm -D --defined-only /usr/lib/libGL.so.1.2.0 | awk '{print $3}' | grep -E '^gl' | sort -u > /tmp/glsyms.txt && \
	{ printf '#include <stddef.h>\ntypedef void (*glstub_t)(void);\nglstub_t glXGetProcAddress(const char *name) { return NULL; }\nglstub_t glXGetProcAddressARB(const char *name) { return NULL; }\n'; \
	  grep -v '^glXGetProcAddress' /tmp/glsyms.txt | sed 's/^/void /; s/$/(void) {}  /'; } > /tmp/genstub.c && \
	gcc -O2 -shared -fPIC -o /tmp/libGL-stub.so.1 /tmp/genstub.c && \
	rm -f /usr/lib/libGL.so.1 /usr/lib/libGL.so.1.2.0 \
		/usr/lib/libGLESv1_CM.so.1 /usr/lib/libGLESv1_CM.so.1.1.0 \
		/usr/lib/libGLESv2.so.2 /usr/lib/libGLESv2.so.2.0.0 \
		/usr/lib/libEGL.so.1 /usr/lib/libEGL.so.1.0.0 \
		/usr/lib/libgallium-*.so /usr/lib/libLLVM.so.22.1 /usr/lib/libLLVM-22.so \
		/usr/lib/libSPIRV-*.so /usr/lib/libelf.so.1 /usr/lib/libelf-0.195.so \
		/usr/lib/libdrm_amdgpu* /usr/lib/libdrm_intel* /usr/lib/libdrm_etnaviv* \
		/usr/lib/libdrm_exynos* /usr/lib/libdrm_freedreno* /usr/lib/libdrm_nouveau* \
		/usr/lib/libdrm_omap* /usr/lib/libdrm_radeon* /usr/lib/libdrm_tegra* \
		/usr/lib/xorg/modules/extensions/libglx.so \
		/usr/lib/xorg/modules/libglamoregl.so && \
	install -m 755 /tmp/libGL-stub.so.1 /usr/lib/libGL.so.1 && \
	apk del gcc musl-dev binutils >/dev/null && \
	rm -f /tmp/genstub.c /tmp/libGL-stub.so.1 /tmp/glsyms.txt && \
	rm -rf /usr/lib/llvm22

# IDLE without the 85 MiB python3-tests dependency: `python3-idle` (community)
# hard-depends on python3-tests, so extract just the binary + idlelib from the
# package instead of installing it (verified the same packaging on v3.24:
# 3.14.7-r0 ships /usr/bin/idle3.14 only — there is no `idle3`).
RUN apk fetch --no-cache python3-idle && \
	mkdir -p /tmp/idle && \
	tar -xzf python3-idle-*.apk -C /tmp/idle && \
	cp -a /tmp/idle/usr/bin/idle3.14 /usr/bin/ && \
	cp -a /tmp/idle/usr/lib/python3.14/idlelib /usr/lib/python3.14/ && \
	rm -rf /tmp/idle python3-idle-*.apk

# Tcl/Tk workaround (plans/display-bug.md §2.8): override the apk libtcl8.6.so
# with a locally-built copy from third_party/tcl-8.6.17 + the
# tcl-notifier-stale-fdset.patch. The override MUST be built from the exact
# apk version (the apk init.tcl demands `package require -exact Tcl 8.6.17`;
# the earlier 8.6.18-built lib aborted tkinter — plans/update-to-latest.md
# §9.2.1; see third_party/README.md). The fix makes the Unix notifier honour
# select()'s return value (CheerpX leaves stale fd sets populated on a 0
# return), so Tcl_DoOneEvent(TCL_DONT_WAIT) drains and tkinter's
# window.update() terminates.
COPY trace/libtcl8.6.so.patched /tmp/libtcl8.6.so.patched
RUN cp -f /tmp/libtcl8.6.so.patched /usr/lib/libtcl8.6.so && rm /tmp/libtcl8.6.so.patched

# Curriculum is stdlib-only (Decision 10): no py3-numpy/matplotlib/requests/
# pytest. py3-pip is kept so `pip` exists and the environment looks normal.
# (There is no runtime pip install — the guest has no internet.)
# py3-pillow/py3-mistune are NOT curriculum: they are the Tk file viewer's
# image/Markdown engines (added 2026-08-16, see plans/future-feature-ideas.md).
RUN ln -sf /usr/bin/pip3 /usr/bin/pip

# Sync agent (samba/webdav modes only)
COPY sync/sync.py /usr/local/lib/webvm-sync/sync.py
COPY sync/sync-home.sh /usr/local/bin/sync-home.sh
RUN if [ "$STORAGE_BACKEND" = "samba" ] || [ "$STORAGE_BACKEND" = "webdav" ]; then \
		chmod +x /usr/local/bin/sync-home.sh && \
		if [ "$STORAGE_BACKEND" = "samba" ]; then \
			pip3 install --no-cache-dir --break-system-packages pysmb==1.2.15; \
		fi; \
	else \
		rm -f /usr/local/lib/webvm-sync/sync.py /usr/local/bin/sync-home.sh; \
	fi

# Users/groups: the desktop runs as 'user' (Xorg refuses to run as root).
RUN adduser -D -s /bin/ash user && \
	addgroup user video && \
	addgroup user input && \
	addgroup user tty && \
	echo 'root:webvm' | chpasswd && \
	echo 'user:user' | chpasswd

# Bake /root/.syncrc (and a user-owned copy the agent can read at
# /home/user/.syncrc) from build args so the fallback is functional out of the
# box (runtime /opt/syncrc overrides without a rebuild).
RUN if [ "$STORAGE_BACKEND" = "samba" ]; then \
		printf 'backend = samba\nhost = %s\nshare = %s\nuser = %s\npassword = %s\n' \
			"$SAMBA_HOST" "$SAMBA_SHARE" "$SAMBA_USER" "$SAMBA_PASS" > /root/.syncrc; \
		cp /root/.syncrc /home/user/.syncrc; \
		chown user:user /home/user/.syncrc; \
	elif [ "$STORAGE_BACKEND" = "webdav" ]; then \
		printf 'backend = webdav\nurl = %s\nuser = %s\npassword = %s\n' \
			"$SYNC_URL" "$SYNC_USER" "$SYNC_PASS" > /root/.syncrc; \
		cp /root/.syncrc /home/user/.syncrc; \
		chown user:user /home/user/.syncrc; \
	fi

# Git tooling: no remotes preconfigured (remotes are added in-guest later via
# the gateway's tailnet IP on the 2222 relay). accept-new host keys. The
# ed25519 keypair is generated at FIRST BOOT (desktop.start) — never baked
# into the image, which is served unauthenticated to browsers.
COPY rootfs/home/user/.gitconfig /home/user/.gitconfig
# /run dirs for OpenRC 0.63 (plans/update-to-latest.md Tier B): openrc's
# rc_dirfd(open("/run/openrc")) crashes when the dir is absent (the 0.60s
# svcdir move from /var/run to /run), and the emulated guest cannot mount a
# tmpfs on /run (init.sh is patched — it no longer exits when the mount
# fails). Keep /run as a plain directory with the subdirs openrc needs.
# IMPORTANT: ALL state dirs must pre-exist — openrc's rc_dirfd falls back to
# mkdirat+openat for a missing dir, and under CheerpX that retry path can
# leave dirfds[] == -1, after which faccessat(-1) hangs the emulator forever
# (diagnosed 2026-08-19: the boot stalled at the deptree state scan).
RUN mkdir -p /run/openrc /run/openrc/starting /run/openrc/started \
		/run/openrc/stopping /run/openrc/inactive /run/openrc/wasinactive \
		/run/openrc/failed /run/openrc/hotplugged /run/openrc/daemons \
		/run/openrc/options /run/openrc/exclusive /run/openrc/scheduled \
		/run/openrc/init.d /run/openrc/tmp \
		/run/lock /run/secrets && \
	chmod 0750 /run/openrc && \
	chmod 0770 /run/lock

RUN mkdir -p /home/user/.ssh && \
	chmod 700 /home/user/.ssh && \
	printf 'Host *\n\tStrictHostKeyChecking accept-new\n' > /home/user/.ssh/config && \
	chown -R user:user /home/user/.ssh && \
	chown user:user /home/user/.gitconfig

# Desktop configs (xinitrc execs openbox, openbox's autostart runs the file
# explorer + keep-alive; rc.xml maximizes every window and adds a titlebar ✕).
COPY config/xinitrc /home/user/.xinitrc
COPY config/.Xresources /home/user/.Xresources
COPY config/openbox/rc.xml /home/user/.config/openbox/rc.xml
COPY config/openbox/menu.xml /home/user/.config/openbox/menu.xml
COPY config/openbox/autostart /home/user/.config/openbox/autostart
RUN chmod +x /home/user/.xinitrc && \
	chmod 755 /home/user/.config/openbox/autostart && \
	chown -R user:user /home/user/.config /home/user/.xinitrc /home/user/.Xresources

# File explorer (the desktop client): a stdlib-only Tk app autostarted by
# Openbox on
# the user's home directory. "Open with IDLE" / double-clicking a .py launches
# IDLE via /usr/local/bin/idle3.14-launcher and REPLACES the explorer on screen
# until IDLE exits. The keep-alive daemon relaunches it whenever the last
# window closes. The tests are installed alongside so the rootfs smoke suite
# can run them in-guest under Xvfb.
COPY scripts/file-explorer.py /usr/local/bin/file-explorer.py
COPY scripts/file-explorer-tests.py /usr/local/bin/file-explorer-tests.py
# Window-count helper: Openbox (unlike the old i3) has no tree IPC, so the
# keep-alive daemon counts the WM's client windows via xprop below (pure
# shell — no Python interpreter spawn on every 3 s poll; the old
# wm-clients.py is gone). The file explorer itself reads the same
# _NET_CLIENT_LIST property in-process (file-explorer.py `_wm_client_windows`).
# Installed for the desktop and, for the rootfs smoke suite, the in-guest tests.
COPY scripts/wm-clients.sh /usr/local/bin/wm-clients.sh
# Shared file-type contract (explorer routing + viewer rendering) — importable
# from /usr/local/bin by both scripts (underscore name: it is a module, not
# an executable).
COPY scripts/file_types.py /usr/local/bin/file_types.py
# Tk file viewer (images/text/Markdown) + its test suite. The explorer
# launches it for every non-Python file and swaps screens with it, exactly
# like IDLE (the keep-alive daemon guards it like IDLE too).
COPY scripts/file-viewer.py /usr/local/bin/file-viewer.py
COPY scripts/file-viewer-tests.py /usr/local/bin/file-viewer-tests.py
COPY rootfs/usr/local/bin/open-file-explorer.sh /usr/local/bin/open-file-explorer.sh
COPY rootfs/usr/local/bin/keep-file-explorer.sh /usr/local/bin/keep-file-explorer.sh
RUN chmod +x /usr/local/bin/open-file-explorer.sh /usr/local/bin/keep-file-explorer.sh /usr/local/bin/file-viewer.py /usr/local/bin/wm-clients.sh

# A starter .py in the home directory: new users can double-click it to try
# IDLE right away, and the smoke suite's real-IDLE-launch check uses it.
COPY rootfs/home/user/hello.py /home/user/hello.py
# The welcome README shown in the file manager's home folder (explicit COPY,
# like hello.py — rootfs/home/user is not synced wholesale into the image).
COPY rootfs/home/user/Readme.md /home/user/Readme.md
RUN chown user:user /home/user/hello.py /home/user/Readme.md

# Curriculum examples: bake the examples directory into the user's home.
# It is READ-ONLY in the image (0555 dir / 0444 files, owner 'user') so the
# guest FS refuses writes even through the IndexedDB overlay — the examples are
# reference material to copy, never to edit in place.
COPY examples/ /home/user/examples/
RUN chown -R user:user /home/user/examples && \
	chmod 0555 /home/user/examples && \
	chmod 0444 /home/user/examples/*

# Display auto-resize support (matches the X resolution to the KMS canvas)
COPY scripts/99-screen-resize.sh /etc/X11/xinit/xinitrc.d/99-screen-resize.sh
RUN chmod +x /etc/X11/xinit/xinitrc.d/99-screen-resize.sh

# Root filesystem overlays: hosts file, network interfaces, desktop boot,
# inittab (no gettys — single-user autologin desktop, no console login prompt)
COPY rootfs/etc/hosts /etc/hosts
COPY rootfs/etc/inittab /etc/inittab
COPY rootfs/etc/network/interfaces /etc/network/interfaces
COPY rootfs/etc/local.d/desktop.start /etc/local.d/desktop.start
RUN chmod +x /etc/local.d/desktop.start
COPY rootfs/usr/local/bin/idle3.14-launcher /usr/local/bin/idle3.14-launcher
# Per-boot loopback verdict for the IDLE launcher (see idle-loopback-cache):
# computed once at desktop.start, consulted on every IDLE launch.
COPY rootfs/usr/local/bin/idle-loopback-cache /usr/local/bin/idle-loopback-cache
RUN chmod +x /usr/local/bin/idle3.14-launcher /usr/local/bin/idle-loopback-cache
# LD_PRELOAD bootstrap for the openrc boot chain (inittab sysinit/boot/
# default lines run through this wrapper — busybox init cannot set env)
COPY rootfs/usr/local/sbin/rc-preload /usr/local/sbin/rc-preload
RUN chmod 0755 /usr/local/sbin/rc-preload
# rc_env_allow: openrc's env_filter() strips LD_PRELOAD from the exec'd
# init scripts' environment, which made them crash on faccessat(-1) under
# CheerpX (no shim loaded). Keep it through (Tier B; see faccessat-fix.c).
RUN cat >> /etc/rc.conf <<'EOF'

# Tier B (CheerpX guest): keep LD_PRELOAD through openrc env_filter() so the
# exec'd init scripts inherit the faccessat/sigprocmask fix shim
# (diskimage/faccessat-fix.c, plans/update-to-latest.md Tier B 9.2).
rc_env_allow="LD_PRELOAD"
EOF
# OpenRC 0.63 bootstrap with the /run tmpfs-mount failure made non-fatal
# (plans/update-to-latest.md Tier B; see the file header for the diff)
COPY rootfs/usr/libexec/rc/sh/init.sh /usr/libexec/rc/sh/init.sh
RUN chmod 0755 /usr/libexec/rc/sh/init.sh
COPY rootfs/etc/X11/xorg.conf.d/10-cheerpx.conf /etc/X11/xorg.conf.d/10-cheerpx.conf
# Static input device config (Tier B): the udev input backend finds nothing
# under CheerpX (shallow emulated sysfs), so the emulated PS/2 mouse and
# keyboard are attached via explicit evdev InputDevice sections
# (plans/update-to-latest.md Tier B §9.2).
COPY rootfs/etc/X11/xorg.conf /etc/X11/xorg.conf
# Fontconfig aliases for common missing font families (consolas, arial, ...)
# -> the installed DejaVu families (deterministic Tk/Xft font resolution).
COPY rootfs/etc/fonts/conf.d/99-webvm-aliases.conf /etc/fonts/conf.d/99-webvm-aliases.conf

# Global time.sleep patch for the CheerpX guest (see the file header):
# site.py imports sitecustomize in EVERY interpreter — sync agent, explorer,
# viewer, IDLE (-n user code included), any student script.
COPY rootfs/usr/lib/python3.14/site-packages/sitecustomize.py /usr/lib/python3.14/site-packages/sitecustomize.py

# CRITICAL FIX (2026-08-10, root cause of the display bug): `docker build`
# creates a `/.dockerenv` file in the container rootfs. The `docker export`
# that produces the guest ext2 carries it over, and OpenRC's rc reads
# `/.dockerenv` to detect a docker container, setting `rc_sys` so that all
# `keyword -containers` services (udev, udev-trigger, udev-settle) are SKIPPED.
# Without udevd, X never discovers the virtual input devices, the cursor
# freezes, and the CheerpX runtime stops presenting frames after the initial
# mode-set. The reference image has no `/.dockerenv`; we must remove it.
RUN rm -f /.dockerenv /.dockerinit

# Record the backend so /etc/local.d/desktop.start can branch on it
RUN echo "$STORAGE_BACKEND" > /etc/webvm-backend

# OpenRC services: udev for the emulated DRM/input devices, dbus for the
# session bus, and the 'local' service so /etc/local.d/*.start runs.
# NOTE (Tier B): udev-trigger/udev-settle are deliberately NOT added, and
# `networking` is removed from the boot runlevel:
#   * Under CheerpX the device nodes already exist (the runtime creates
#     /dev/input/event* itself); the trigger+settle uevent re-processing
#     makes udevd churn the emulated event stream (its "Validate module
#     index" loop never settles) and `udevadm settle` hangs the boot at
#     "Waiting for uevents".
#   * `networking` WANTs dev-settle (pulling the hang back in) and its
#     ioctl-based ifup cannot work under CheerpX anyway ("ip: ioctl
#     SIOCSIFFLAGS failed"). The guest NIC is configured by
#     /etc/local.d/desktop.start's eth0 retry loop + udhcpc — the
#     established data-path mechanism (plans/networking-bug.md).
#   * udevd itself stays up (sysinit) so Xorg's udev monitor finds the
#     input devices (verified 2026-08-20).
RUN rc-update add bootmisc boot && \
	rc-update add udev sysinit && \
	rc-update add udev-postmount default && \
	rc-update add dbus default && \
	rc-update add local default

# Bake the dependency cache (plans/update-to-latest.md Tier B): openrc
# regenerates the deptree on the first boot under CheerpX, and that path
# (deep_mtime_check + utimensat to a bogus future mtime from the skew
# handling) leaves the runtime in a state where the first service spawn
# crashes CheerpX's execve emulation (wasm "function signature mismatch",
# Fault addr==ip==0xffff9fa7; diagnosed 2026-08-20). Running sysinit HERE
# (under docker, where everything works) writes /run/openrc/deptree with a
# sane mtime; the exported image then ships it, the guest's deptree is
# newer than every init script (2026-06-14), and openrc never regenerates
# or adjusts — the skew path is never taken and the spawn works. sysinit
# only: running the boot runlevel too would pre-mark its services (incl.
# networking, which configures eth0 for the tailnet data path) as
# started/failed and the guest would skip them. Failures here are expected
# (mount/proc cannot work in the build container) and harmless; the cache
# + state dirs are what we keep.
RUN /sbin/openrc sysinit; true

# /etc/resolv.conf: Docker's container-managed bind mount cannot be written or
# removed at build time, but `docker export` produces an EMPTY resolv.conf in
# the exported rootfs — exactly right for the guest, which uses IP addresses
# only (MagicDNS off, no guest DNS).

# Size trimming
RUN rm -rf /usr/share/doc /usr/share/man /usr/share/info /var/cache/apk/*

# Size trimming (round 2, 2026-08-21): components verified unused at runtime.
#   * /usr/share/mime — only libgio's g_content_type_guess reads it; no guest
#     binary calls that path (openbox menu has no icons, the explorer sniffs
#     file types itself, xterm less/vi do not need the MIME DB).
#   * /usr/libexec/glycin-loaders — gdk-pixbuf's on-demand image loaders,
#     dlopen'd only when an image is actually decoded; openbox's Clearlooks
#     theme is color-only and the menu has no icons, so nothing decodes.
#   * DejaVu faces not reachable through 99-webvm-aliases.conf (which maps
#     only to Sans/Sans Mono/Serif regular+bold+italic): Condensed,
#     ExtraLight, MathTeX.
#   * /usr/share/hwdata (pci.ids) — libpciaccess uses it only for device-name
#     labels; Xorg's modesetting driver works without it.
#   * /usr/lib/girepository-1.0 typelibs — no pygobject/gjs in the guest.
#   * libepoxy + libwayland-client — orphaned by the mesa/GL stack removal
#     (no DT_NEEDED or dlopen users remain, verified 2026-08-21).
#   * Tcl extras (itcl/thread/tdbc) — optional Tcl packages, tkinter never
#     loads them.
#   * imlib2 CLI tools, non-Clearlooks openbox themes, mesa leftovers
#     (drirc.d) and build-time aclocal macros, fontconfig cache (regenerated
#     on demand).
#   * Python __pycache__ is NOT removed — it is PRE-BAKED with compileall.
#     The original round-2 trim deleted __pycache__ on the theory that the
#     interpreter regenerates it on demand, but that made the FIRST import on
#     a fresh boot recompile every stdlib module onto the SLOW overlay
#     (measured: first `import tkinter` 1.12 s with no pyc vs 0.30 s with
#     baked pyc — plans/update-to-latest.md §9.5.1 item 11; idlelib and the
#     file explorer import far more and paid even more). compileall prebakes
#     bytecode matching the exact installed 3.14 interpreter (+~5.5 MiB),
#     so first-run imports read cached .pyc instead of recompiling.
RUN python3 -m compileall -q -j 4 /usr/lib/python3.14 && \
	rm -rf /usr/share/mime /usr/libexec/glycin-loaders /usr/share/hwdata \
		/usr/lib/girepository-1.0 /usr/share/drirc.d /usr/share/aclocal \
		/var/cache/fontconfig \
		/usr/lib/itcl4.3.4 /usr/lib/thread2.8.12 /usr/lib/tdbc1.1.12 \
		/usr/lib/tdbcmysql1.1.12 /usr/lib/tdbcodbc1.1.12 /usr/lib/tdbcpostgres1.1.12 \
		/usr/lib/libepoxy.so.0 /usr/lib/libepoxy.so.0.0.0 \
		/usr/lib/libwayland-client.so.0 /usr/lib/libwayland-client.so.0.25.0 \
		/usr/bin/imlib2_* \
		/usr/share/fonts/dejavu/DejaVuMathTeXGyre.ttf \
		/usr/share/fonts/dejavu/DejaVuSans-ExtraLight.ttf \
		/usr/share/fonts/dejavu/DejaVuSansCondensed*.ttf \
		/usr/share/fonts/dejavu/DejaVuSerifCondensed*.ttf && \
	find /usr/share/themes -mindepth 1 -maxdepth 1 -type d ! -name Clearlooks \
		-exec rm -rf {} +

CMD ["/sbin/init"]
