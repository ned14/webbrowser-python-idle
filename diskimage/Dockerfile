# WebVM guest image: minimal i386 Alpine 3.17 with stdlib-only Python + IDLE
# (launched from the file explorer — a stdlib-only Tk app that autostarts at
# desktop boot and is replaced on screen by IDLE while IDLE runs),
# an Xorg/i3 desktop, terminal, git tooling, and (for
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

FROM docker.io/i386/alpine:3.17

ARG STORAGE_BACKEND=browser
ARG SYNC_URL=http://100.64.0.1:8082/webdav/
ARG SYNC_USER=webdav
ARG SYNC_PASS=changeme
ARG SAMBA_HOST=100.64.0.1
ARG SAMBA_SHARE=share
ARG SAMBA_USER=user
ARG SAMBA_PASS=changeme

# v3.17 is EOL: rewrite BOTH repositories (don't append) so the community repo
# is enabled and the exact EOL pins are used. dl-cdn.alpinelinux.org is the
# resolving mirror (still serving v3.17 at pin time); if it ever stops serving
# EOL versions, switch to https://archive.alpinelinux.org/alpine/v3.17/.
RUN cat > /etc/apk/repositories <<'EOF'
https://dl-cdn.alpinelinux.org/alpine/v3.17/main
https://dl-cdn.alpinelinux.org/alpine/v3.17/community
EOF

# Desktop + tooling. python3-tkinter and the idlelib extraction come from
# community; everything else from main. xvfb is installed for the
# in-guest GUI test run (tests/rootfs/smoke.sh runs the file-explorer test
# suite under Xvfb) — it is not used by the desktop itself.
RUN apk add --no-cache \
	alpine-base \
	udev-init-scripts \
	udev-init-scripts-openrc \
	eudev \
	xorg-server \
	xvfb \
	xinit \
	xf86-input-libinput \
	xrandr \
	i3wm \
	font-dejavu \
	python3 \
	python3-tkinter \
	xterm \
	git \
	openssh-client-default \
	busybox-extras \
	dbus \
	py3-pip

# IDLE without the 85 MiB python3-tests dependency: `python3-idle` (community)
# hard-depends on python3-tests, so extract just the binary + idlelib from the
# package instead of installing it. The package ships /usr/bin/idle3.10 only —
# there is no `idle3`.
RUN apk fetch --no-cache python3-idle && \
	mkdir -p /tmp/idle && \
	tar -xzf python3-idle-*.apk -C /tmp/idle && \
	cp -a /tmp/idle/usr/bin/idle3.10 /usr/bin/ && \
	cp -a /tmp/idle/usr/lib/python3.10/idlelib /usr/lib/python3.10/ && \
	rm -rf /tmp/idle python3-idle-*.apk

# Tcl/Tk workaround (plans/display-bug.md §2.8): override the apk libtcl8.6.so
# with a locally-built copy from third_party/tcl-8.6.12 + alpine/*.patch. The
# fix makes the Unix notifier honour select()'s return value (CheerpX leaves
# stale fd sets populated on a 0 return), so Tcl_DoOneEvent(TCL_DONT_WAIT)
# drains and tkinter's window.update() terminates.
COPY trace/libtcl8.6.so.patched /tmp/libtcl8.6.so.patched
RUN cp -f /tmp/libtcl8.6.so.patched /usr/lib/libtcl8.6.so && rm /tmp/libtcl8.6.so.patched

# Curriculum is stdlib-only (Decision 10): no py3-numpy/matplotlib/requests/
# pytest. py3-pip is kept so `pip` exists and the environment looks normal.
# (There is no runtime pip install — the guest has no internet.)
RUN ln -sf /usr/bin/pip3 /usr/bin/pip

# Sync agent (samba/webdav modes only)
COPY sync/sync.py /usr/local/lib/webvm-sync/sync.py
COPY sync/sync-home.sh /usr/local/bin/sync-home.sh
RUN if [ "$STORAGE_BACKEND" = "samba" ] || [ "$STORAGE_BACKEND" = "webdav" ]; then \
		chmod +x /usr/local/bin/sync-home.sh && \
		if [ "$STORAGE_BACKEND" = "samba" ]; then \
			pip3 install --no-cache-dir pysmb==1.2.10; \
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
RUN mkdir -p /home/user/.ssh && \
	chmod 700 /home/user/.ssh && \
	printf 'Host *\n\tStrictHostKeyChecking accept-new\n' > /home/user/.ssh/config && \
	chown -R user:user /home/user/.ssh && \
	chown user:user /home/user/.gitconfig

# Desktop configs (xinitrc execs i3, i3 autostarts the file explorer)
COPY config/xinitrc /home/user/.xinitrc
COPY config/.Xresources /home/user/.Xresources
COPY config/i3/config /home/user/.config/i3/config
RUN chmod +x /home/user/.xinitrc && \
	chown -R user:user /home/user/.config /home/user/.xinitrc /home/user/.Xresources

# File explorer (the desktop client): a stdlib-only Tk app autostarted by i3 on
# the user's home directory. "Open with IDLE" / double-clicking a .py launches
# IDLE via /usr/local/bin/idle3.10-launcher and REPLACES the explorer on screen
# until IDLE exits. The keep-alive daemon relaunches it whenever the last
# window closes. The tests are installed alongside so the rootfs smoke suite
# can run them in-guest under Xvfb.
COPY scripts/file-explorer.py /usr/local/bin/file-explorer.py
COPY scripts/file-explorer-tests.py /usr/local/bin/file-explorer-tests.py
COPY rootfs/usr/local/bin/open-file-explorer.sh /usr/local/bin/open-file-explorer.sh
COPY rootfs/usr/local/bin/keep-file-explorer.sh /usr/local/bin/keep-file-explorer.sh
RUN chmod +x /usr/local/bin/open-file-explorer.sh /usr/local/bin/keep-file-explorer.sh

# A starter .py in the home directory: new users can double-click it to try
# IDLE right away, and the smoke suite's real-IDLE-launch check uses it.
COPY rootfs/home/user/hello.py /home/user/hello.py
RUN chown user:user /home/user/hello.py

# Curriculum examples: bake the python-examples directory into the user's home.
# It is READ-ONLY in the image (0555 dir / 0444 files, owner 'user') so the
# guest FS refuses writes even through the IndexedDB overlay — the examples are
# reference material to copy, never to edit in place.
COPY python-examples/ /home/user/python-examples/
RUN chown -R user:user /home/user/python-examples && \
	chmod 0555 /home/user/python-examples && \
	chmod 0444 /home/user/python-examples/*

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
COPY rootfs/usr/local/bin/idle3.10-launcher /usr/local/bin/idle3.10-launcher
RUN chmod +x /usr/local/bin/idle3.10-launcher
COPY rootfs/etc/X11/xorg.conf.d/10-cheerpx.conf /etc/X11/xorg.conf.d/10-cheerpx.conf
# Fontconfig aliases for common missing font families (consolas, arial, ...)
# -> the installed DejaVu families (deterministic Tk/Xft font resolution).
COPY rootfs/etc/fonts/conf.d/99-webvm-aliases.conf /etc/fonts/conf.d/99-webvm-aliases.conf

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
# session bus, the 'local' service so /etc/local.d/*.start runs, and networking.
RUN rc-update add bootmisc boot && \
	rc-update add udev sysinit && \
	rc-update add udev-trigger sysinit && \
	rc-update add udev-settle sysinit && \
	rc-update add udev-postmount default && \
	rc-update add dbus default && \
	rc-update add local default && \
	rc-update add networking boot

# /etc/resolv.conf: Docker's container-managed bind mount cannot be written or
# removed at build time, but `docker export` produces an EMPTY resolv.conf in
# the exported rootfs — exactly right for the guest, which uses IP addresses
# only (MagicDNS off, no guest DNS).

# Size trimming
RUN rm -rf /usr/share/doc /usr/share/man /usr/share/info /var/cache/apk/*

CMD ["/sbin/init"]
