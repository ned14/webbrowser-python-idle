# Implementation Options: Linux Desktop in the Browser via Docker

Research date: 2026-08-08

## Goal

A Docker container that serves a website which runs a copy of Linux inside the
web browser, with graphical output so users get a full desktop experience.

## Two Architecture Families

The requirement can be read two ways, and each produces a different design:

| Family | How "Linux runs in the browser" works | Server role |
|---|---|---|
| **A. Client-side virtualization (WebAssembly)** | The OS boots in the browser via a WASM x86 emulator/JIT. The server is only a static web server. | Serves the web app + the Linux disk image. No per-user containers. |
| **B. Server-side Linux, streamed to browser** | The container actually runs a full Linux + desktop environment; the browser is a remote-desktop client (WebSocket VNC / WebRTC / WebCodecs). | Runs one container per user/session, encodes the framebuffer, streams it. |

"Runs a copy of Linux within the web browser" literally describes Family A, but
the phrase "Docker container which serves a website" fits both. Family B is the
only realistic route to a *fast* full desktop on modest client hardware, which
is why almost all production "Linux desktop in the browser" products (Kasm,
Webtop, Selkies) use it.

---

## Family A: Client-side (WebAssembly) Options

### A1. WebVM (CheerpX) — leaningtech/webvm

- URL: https://github.com/leaningtech/webvm — demo https://webvm.io
- **Full graphical desktop**: yes. The Alpine graphical build (Xorg + i3 window
  manager) runs at https://webvm.io/alpine.html. Image repo:
  https://github.com/leaningtech/alpine-image (Alpine + Xorg/i3 desktop).
- How it works: **CheerpX** engine — x86-to-WASM JIT compiler, virtual
  block-based file system (`.ext2` disk image), Linux syscall emulator,
  lwIP TCP/IP stack. Runs unmodified Debian/Alpine binaries.
- Docker role: builds the disk image from a Dockerfile (see `dockerfiles/` and
  `custom-disk-images/` in the repo; `debian_mini`, `debian_large`), then a
  container serves the built site via nginx. Existing `nginx.conf` serves port
  8081. Custom image workflow: "Mini.WebVM" — build `.ext2` from a Dockerfile.
- Networking: Tailscale integration (client connects the VM to your network /
  internet via an exit node). ICMP/ping unavailable.
- License: WebVM Apache-2.0, **but CheerpX is free for individuals/exploration
  only** — organizational use and self-hosting requires a commercial license
  from Leaning Technologies. This is a real blocker for production/company use.
- Performance: near-native for x86 code paths that JIT well; no GPU; graphics
  go through Xorg to a virtual framebuffer, so the desktop feels usable but not
  hardware-accelerated.
- Fit: best match for the literal "Linux runs in the browser" reading and for
  the "Dockerfile → disk image → container serving the site" pipeline.

### A2. v86 — copy/v86

- URL: https://github.com/copy/v86 — demo https://copy.sh/v86
- **Full graphical desktop**: yes — full VGA/SVGA emulation with Bochs VBE
  extensions; framebuffer rendered to a `<canvas>`. Demo profiles include
  multiple Linux distros, FreeBSD, etc. Runs Linux well; 32-bit kernels only
  (no x86-64). Alpine Linux image can be built **from a Dockerfile**
  (`tools/docker/alpine/`, uses Buildroot-style setup). Docs include
  "Linux rootfs on 9p" and "Alpine Linux guest setup".
- How it works: full x86 PC emulator (CPU roughly Pentium 4 / SSE3 level) with
  x86-to-WASM JIT; emulates VGA, PS/2, IDE, NE2000 NIC, virtio (fs/network/
  balloon), SoundBlaster 16. Embeddable via `libv86.js` or `npm install v86`.
- Docker role: repo ships a Docker dev/build/test image
  (`tools/docker/exec/Dockerfile`); a container can build v86 and serve the app
  (`docker run -p 8000:8000`). Your deliverable container would bundle the v86
  web app + a disk image (buildroot/Alpine `.img`/`.iso`).
- Networking: virtio-net / NE2000 with WebSocket proxy; examples include
  fetch-based TCP terminal and Broadcast-Channel networking between tabs.
- License: BSD-2-Clause — fully open source, no commercial licensing trap.
- Performance: slowest real x86 workloads (software emulation); fine for a
  lightweight desktop (e.g., Alpine + a lightweight WM), not for heavy apps.
- Fit: the fully-open, self-contained option; good if the desktop can be a
  lightweight distro and speed is acceptable.

### A3. CheerpX directly / other WASM OS emulators

- CheerpX (https://cheerpx.io) is the commercial engine underlying WebVM and can
  be licensed directly to embed Linux-in-WASM in your own site. Not recommended
  unless you want to pay and need deeper control than WebVM's fork/deploy.
- Others (jor1k, jslinux, WASI-based "containers") are terminal-only or don't
  provide a realistic desktop; listed only for completeness. Docker Desktop's
  "WASM workloads" (https://docs.docker.com/desktop/wasm/) runs WASI modules,
  not a Linux GUI desktop, and is now **deprecated** — not viable for this goal.

---

## Family B: Server-Side Linux Streamed to Browser

### B1. linuxserver/webtop (recommended quick-start)

- URL: https://github.com/linuxserver/docker-webtop
- Prebuilt images with full desktop environments: XFCE, KDE Plasma, MATE, i3
  across Alpine, Ubuntu, Debian, Arch, Fedora (e.g. `lscr.io/linuxserver/webtop:alpine-i3`).
- How it works: the container runs the desktop (now a **Wayland stack** by
  default; X11 fallback) and streams it to the browser. Based on the
  linuxserver Selkies base image (`docker-baseimage-selkies`) — WebCodecs
  H.264/video streaming over WebSocket to an HTML5 client, clipboard, file
  transfer, audio, microphone, gamepad. HTTP on 3000, HTTPS on 3001.
- Run: `docker run -d -p 3000:3000 --shm-size="1gb" lscr.io/linuxserver/webtop:latest`
  (HTTP) — zero-build path to a full desktop in a browser.
- GPU acceleration: `/dev/dri` mount with `AUTO_GPU=true` / `DRINODE` /
  `DRI_NODE`; Zero-Copy encode for Intel/AMD; Nvidia supported via driver ≥580
  and `nvidia-drm.modeset=1`. No GPU → CPU encoding fallback.
- Security: image warns it gives the browser user root-in-container (passwordless
  sudo in the web terminal); do not expose to the internet without auth
  (CUSTOM_USER/PASSWORD, reverse proxy like SWAG) and hardening vars.
- License: GPL-3.0. Actively maintained, multi-arch.
- Fit: fastest path to "Linux desktop in a browser" served by a Docker
  container, with GPU acceleration and a full KDE/XFCE/i3 experience.

### B2. Kasm Workspaces + KasmVNC

- URLs: https://kasmweb.com | KasmVNC https://github.com/kasmtech/KasmVNC |
  workspace images https://github.com/kasmtech/workspaces-images |
  base images https://github.com/kasmtech/workspaces-core-images
- How it works: streaming containers (KasmVNC server + HTML5 client) that
  containerize full desktops (XFCE, KDE, i3, MATE) or single apps. KasmVNC is a
  modern VNC server tuned for the web (WebCodecs H.264/H.265/AV1, WebP/JPEG,
  WebRTC UDP transit, DLP/clipboard features, DRI3 GPU accel). Kasm Workspaces
  is the orchestration platform (images, sessions, auth, security policies) with
  free Community Edition and commercial licensing for teams/production.
- Docker role: a single container runs a desktop and serves KasmVNC's web
  client on a websocket port (typically 6901). The platform adds orchestration,
  database, and agents for multi-user production.
- License: KasmVNC GPL-2.0; platform has free Community Edition, paid for
  commercial production use.
- Fit: the de-facto commercial-grade container-streaming platform; choose when
  you need multi-user isolation, security policies, and orchestration rather
  than a single standalone container.

### B3. Selkies (selkies-project/selkies, formerly selkies-gstreamer)

- URL: https://github.com/selkies-project/selkies
- How it works: open-source, low-latency Linux remote-desktop streaming —
  GStreamer pipeline encodes the X11/Wayland framebuffer and streams over plain
  WebSockets (WebRTC opt-in) to an HTML5 client. GPU/CPU accelerated (Nvidia,
  Intel, AMD), 60+ FPS at Full HD, clipboard, audio, gamepad. Ships a Dockerfile
  and `docker-compose.yml`; designed for containers/K8s/HPC.
- License: MPL-2.0.
- Fit: the building block for a **custom** streaming container if you want
  control over the web UI and encoding stack instead of using webtop/Kasm
  images. It is what linuxserver's webtop now builds on.

### B4. Roll-your-own: X11 + noVNC/x11vnc (or Xvfb + VNC)

- Common pattern: Ubuntu/Arch base + Xvfb or Xorg + a window manager (XFCE/i3/
  KDE) + `x11vnc` + `noVNC`/`websockify` (or TigerVNC + websockify) to expose
  the VNC session over WebSocket with an HTML5 client.
- Docker role: single Dockerfile installs the desktop + VNC server + noVNC;
  expose one HTTP port; entrypoint starts Xvfb → WM → x11vnc → websockify.
- Pros: full control, no vendor licensing. Cons: you own perf tuning, auth,
  clipboard, resize handling, security. noVNC is LGPL; x11vnc GPL-2.0.
- Fit: choose only if the prebuilt options can't be customized enough.

### B5. Other / adjacent

- **Apache Guacamole**: gateway that serves RDP/VNC/SSH in the browser. It
  proxies an *existing* RDP/VNC target rather than running the desktop itself;
  less suited to "container runs Linux with a desktop," but usable as the front
  end for B4-style containers.
- **Docker-in-Docker browser consoles** (e.g. docker-web-console): give you a
  container terminal in the browser, not a graphical Linux desktop — out of
  scope for the "full desktop experience" requirement.

---

## Comparison

| Option | Desktop quality | Client-side vs server-side | GPU accel | License/commercial risk | Effort |
|---|---|---|---|---|---|
| A1 WebVM/CheerpX | Good (Alpine + Xorg/i3) | Client (WASM) | No | Apache-2.0, **CheerpX commercial for orgs** | Medium |
| A2 v86 | OK (lightweight distros only; 32-bit) | Client (WASM) | No | BSD-2-Clause (fully open) | Medium |
| B1 linuxserver/webtop | Excellent (XFCE/KDE/MATE/i3) | Server (streamed) | Yes (/dev/dri, Nvidia) | GPL-3.0 | Low |
| B2 Kasm Workspaces/KasmVNC | Excellent | Server (streamed) | Yes (DRI3) | CE free; commercial for production | Low–Med |
| B3 Selkies | Excellent | Server (streamed) | Yes | MPL-2.0 | Medium |
| B4 Custom X11+noVNC | Good–Excellent | Server (streamed) | Manual | OSS components | High |
| B5 Guacamole | Good (via RDP/VNC) | Server (gateway) | Via target | Apache-2.0 | Medium |

## Key Decision Factors

1. **Interpretation**: "runs a copy of Linux within the web browser" → Family A
   (WebVM or v86). "Full desktop experience" with performance and GPU →
   Family B (webtop / Kasm / Selkies). A hybrid is also possible: serve a
   client-side WASM VM for simplicity, or stream for fidelity.
2. **Licensing**: CheerpX/WebVM self-hosting has a commercial license
   requirement for organizations; v86 is the only fully-open client-side option.
   Kasm is free as Community Edition but commercial for production platforms.
   webtop/Selkies are permissively licensed (GPL/MPL).
3. **Hardware**: Family B benefits from `/dev/dri` GPUs and AVX2 CPUs (Wayland
   stack). Family A only needs a WASM-capable browser; client hardware does the
   work.
4. **Multi-user**: one streaming container per user (Kasm orchestrates this;
   webtop is single-session); client-side WASM has no per-user servers but no
   shared sessions/persistence either.
5. **Security**: any container that exposes a GUI + root shell to the browser
   must sit behind auth and a reverse proxy (see webtop/Kasm hardening docs).

## Suggested Validation Path (for the chosen option)

- Stand up the container locally and open the browser URL.
- Verify a window manager loads, apps render, and the mouse/keyboard/clipboard
  round-trip.
- Load-test a second concurrent session if multi-user is required.
- If GPU acceleration is needed, verify `/dev/dri` passthrough and encoding
  (e.g. `glxinfo`, encode stats in the client sidebar).
- For client-side options, verify the disk image serves correctly and the VM
  boots to the desktop in Firefox and Chromium.
