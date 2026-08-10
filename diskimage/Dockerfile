# WebVM guest image: minimal i386 Alpine 3.17 with stdlib-only Python + IDLE
# (autostarted at desktop boot), an Xorg/i3 desktop, terminal + file manager,
# git tooling, and (for samba/webdav builds) the home-sync agent.
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
# community; everything else from main.
RUN apk add --no-cache \
	alpine-base \
	udev-init-scripts \
	udev-init-scripts-openrc \
	eudev \
	xorg-server \
	xinit \
	xf86-input-libinput \
	xrandr \
	i3wm \
	font-dejavu \
	python3 \
	python3-tkinter \
	xterm \
	pcmanfm \
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

# Desktop configs (xinitrc execs i3, i3 autostarts idle3.10)
COPY config/xinitrc /home/user/.xinitrc
COPY config/.Xresources /home/user/.Xresources
COPY config/i3/config /home/user/.config/i3/config
RUN chmod +x /home/user/.xinitrc && \
	chown -R user:user /home/user/.config /home/user/.xinitrc /home/user/.Xresources

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
COPY rootfs/etc/X11/xorg.conf.d/10-cheerpx.conf /etc/X11/xorg.conf.d/10-cheerpx.conf

# Record the backend so /etc/local.d/desktop.start can branch on it
RUN echo "$STORAGE_BACKEND" > /etc/webvm-backend

# OpenRC services: udev for the emulated DRM/input devices, dbus for GTK apps,
# the 'local' service so /etc/local.d/*.start runs, and networking.
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
