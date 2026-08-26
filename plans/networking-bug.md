# Networking bug: browser-side tailnet (CheerpX wasm) crashes; works upstream

**Status: RESOLVED (2026-08-15/16, §15/§16).** This file is the condensed
record; the full chronological investigation (hypotheses, test matrices,
internet research, session logs) is superseded and lives in git history.

**What actually happened, in one paragraph:** the original crash
(`RuntimeError: function signature mismatch` + `TypeError: e is not a
function`) was never a tailscale.wasm defect — the wasm client was **never
even loaded** (in any configuration, including the reference webvm.io). The
CheerpX core's network-init flow never calls `TailscaleNetwork.up()`; the
deterministic +128s crash was the core's socket dispatch trapping on the
guest's `udhcpc -i eth0 -n` raw socket for a NIC the core never creates.
§15 fixes this (app-side driver + eth0 guard + headscale 0.28.0 pin + port-443
WSS listener); the guest data path then required rebuilding the tailscale
wasm client from source (v1.102.2 + custom MessageChannel tun, §16.1) and
reworking the browser-side wrappers and guest sync agent around CheerpX
process/timer quirks (§16.2/16.3). All of this is recorded in
`plans/webvm_implementation.md` §12/21(27) and §5.

**Read §16 first. §15 is the verified diagnosis; §1–§14 are historical.**

## 1. Summary (historical)

Guest networking exists ONLY via CheerpX's browser-side Tailscale
(`networkInterface`; AGENTS.md non-negotiable). The original failure mode:
CI webdav sync E2E failed (`webvm.lock` never appeared), the browser console
showed `Unexpected exit RuntimeError: function signature mismatch` and
`PAGEERROR: TypeError: e is not a function` about 128 s into the boot, and
the guest's `udhcpc` never brought eth0 up. The §2–§14 narrative blamed the
tailscale wasm (`call_indirect` type mismatch) and headscale's
`/ts2021` Noise-over-WebSocket path (issue #1650); both were wrong (see
§15.1). Two REAL adjacent bugs were found and fixed regardless: control-plane
CORS (§15.2.1) and the headscale client-version gate (§15.2.2), plus the
headscale 0.28.0 pin (see below).

## 2–14. Historical investigation (superseded)

The following sections existed here and were removed as superseded:
symptom/reproduction scripts, the tailnet architecture map (now correct-as-
of-§16 in `webvm/src/lib/network.js` + the plan §5), the "everything already
tried" test matrix, the T1–T13 hypotheses, the open questions, and the
internet research (§13: webvm #222 "wedge symptom" reframe, the headscale
0.29.x `/ts2021`-GET + `/key`-version-gate timeline, wasm_exec.js pairing,
webvm.io status). Their conclusions that still matter are folded in below
(§15.2.2, §16.6). Nothing here is load-bearing for current code; the
committed diagnostic probes (`tests/e2e/repro-tailnet.mjs` and friends) are
kept as engineering tools.

## 15. 2026-08-15 session: verified diagnosis and applied fixes

Everything here was verified empirically in the browser (Chromium 151
headless/headed + Chrome 126, Playwright request/console/WebSocket tracing,
in-page module instrumentation, headscale 0.28.0/0.29.2/0.29.3, runtimes
1.2.8–1.3.8, self-hosted and CDN-served, and a live drive of webvm.io).

### 15.1 What the bug actually is (three stacked failures)

1. **The tailnet NEVER initializes (in any configuration, including the
   reference site).** The CheerpX core's network-init flow stops after
   `autoConf()` resolves and never calls `netExports.up()`: `tailscale.wasm`
   is never fetched, no `/key` request ever reaches the control plane, and
   the client stays in `NoState`. Both runtime paths are affected (with
   `netmapUpdateCb` → the `direct.js` `TailscaleNetwork` path, where nothing
   calls `up()`; without it → the legacy `cheerpOSNetInit` path, whose
   autoConf never resolves past init). webvm.io does the exact same thing
   (2026-08-15, current Chromium, runtime 1.3.8) — the "works upstream /
   broken here" premise was false, and no runtime 1.2.8–1.3.8 fixes it.
2. **The crash is in the CHEERPX CORE, not the tailscale wasm.** The
   `function signature mismatch` is a `call_indirect (type 10)` trap at
   `cxcore.wasm` func[3858] offset `0x1a192d` — a vtable dispatch on a
   null/uninitialized object field: the core's socket backend/netOps was
   never wired. Module IDs are content-derived; the "tailscale wasm" frame
   attribution was an unverified assumption (per webvm #222 this signature is
   also the runtime's general *wedge symptom* for any earlier internal
   failure).
3. **The deterministic +128s trigger is the guest's `udhcpc`, not the sync
   agent.** eth0 never appears (the core never creates the NIC);
   `desktop.start`'s eth0 loop gives up after 120s and `udhcpc -i eth0 -n`
   opens a raw socket on the missing interface → the socket dispatch trap.
   Proven by running WITHOUT the sync agent: the crash still fires at +128s.

### 15.2 Secondary stack issues found and fixed (all verified)

1. **Control-plane CORS (FIXED in `server/nginx.conf.template`):** the wasm
   client fetches `https://<control-host>:8443/key?v=109` cross-origin;
   headscale answers only `/derp/probe` with ACAO. Fix: `add_header
   Access-Control-Allow-Origin $http_origin always;` + `Vary: Origin` on the
   8443 listener (unit test: `test_control_listener_cors_for_wasm_tailscale`;
   the plan's old "no CORS needed" claim was wrong — see plan §6/§12/21(d)).
2. **Headscale version gate:** the bundled wasm client was Tailscale v1.78
   (capver 109); headscale 0.29.x rejects it at `/key` (gate #3391, min
   v1.80/capver 113) AND at `/machine/register` (rejectUnsupported). **Pin
   headscale 0.28.0** (min capver 106, `/ts2021` registered for WebSocket
   GET). Now moot — the rebuilt client (§16.1) reports capver 142; unpinning
   is a one-line change if ever wanted.
3. **The wasm client DROPS the controlUrl port** — it builds Noise-over-WSS
   as `wss://<host>/ts2021` (default 443) and the DERP URLs similarly. Fix:
   nginx `CONTROL_WSS_PORT` (443) listener mirroring 8443 + compose publish +
   CSP `connect-src https://${CONTROL_HOST} wss://${CONTROL_HOST}` entries.
4. **CORS `MultipleAllowOriginValues`:** headscale answers `/derp/probe` with
   `ACAO: *` and nginx echoed `$http_origin` alongside → the browser rejected
   the probe. Fix: `proxy_hide_header Access-Control-Allow-Origin;` + echo
   ONLY when `$http_origin` is non-empty (integration.sh asserts this).
5. **The app must drive the tailnet itself** (the core never does). Fix:
   `webvm/src/lib/network.js` — drop `netmapUpdateCb` (selects the legacy
   path so the core's socket dispatcher uses `a47`), add an app-side driver
   that imports `/cheerpx/tun/tailscale_tun_auto.js`, calls `autoConf()` +
   `up()`, sets the `cjTailscale*` globals, and exposes the socket adapter
   the core's dispatcher calls (`TCPSocket`/`UDPSocket`/`parseIP`/`dumpIP`/
   `up`), mirroring `direct.js`'s wrapper contracts (incl. `close()` returning
   a promise). This driver + adapter is what still ships today.
6. **Guest-side eth0/udhcpc guard** (`desktop.start`): only run `udhcpc`
   when eth0 exists — the deterministic +128s core crash is gone.
7. **sync.py bounded boot wait:** ping fails fast (3s timeout),
   `wait_for_tailnet` checks the return value, 12 attempts ≈ 60s.

### 15.3 Verified end state (2026-08-15)

With the app-side driver: `tailscale.wasm` fetched → `/key?v=109` 200 → WSS
`/ts2021` 101 → registered → netmap → state 6 (**Running**) → DERP WSS → node
ONLINE in `headscale nodes list`; the VM boots to the X desktop in ~70s with
no crash/pageerrors; boot + persistence E2E pass. The guest **data path**
(eth0/connect) was still dead — that is what §16 fixed.

### 15.4 What to do next (superseded)

Reported to Leaning (cheerpx-meta/webvm): the runtime's network-init never
calls `TailscaleNetwork.up()`; the core never creates the guest NIC; guest
SYNs die in the netstack. **Superseded by §16** — this repo no longer depends
on a fix; the reference webvm.io tailnet remains broken in current Chromium
(worth re-confirming with Leaning).

## 16. 2026-08-15 session: the tailscale wasm client is rebuilt from source — RESOLVED

### 16.1 The rebuilt client (the wasm-rebuild lever from the superseded §13 research, executed)

The bundled CheerpX tailscale.wasm (Leaning's private tsconnect fork, v1.78)
was replaced with a **tailscale v1.102.2 build from source** reproducing the
glue's API surface exactly: `scripts/tailscale-wasm-entry/wasm_js.go` (a
custom `//go:build js` entry modelled on `cmd/tsconnect/wasm/wasm_js.go`,
wiring `wgengine.NewUserspaceEngine` + a custom MessageChannel-backed
`tun.Device` + `run`/`up`/`down`/`login`/`logout` + **numeric** `notifyState`
0–6 + tsconnect-style netmap JSON; the stock netstack-based data path is
deliberately NOT used). Built by `scripts/rebuild-tailscale-wasm.sh`
(Docker `golang:1.26.6`, shallow clone tailscale @ v1.102.2, `GOOS=js
GOARCH=wasm`, matching `wasm_exec.js` from the toolchain). **No changes to
`tailscale_tun.js`/`tailscale_tun_auto.js`**; the app-side driver (network.js)
still drives the tailnet. Capver 142; logtail uploads removed from the
rebuilt client (fully self-hosted tailnet must not phone Tailscale's cloud —
the CSP blocked it anyway).

### 16.2 Browser-side data path fixes (webvm/src/lib/network.js)

Three wrapper bugs killed the guest data path once the client ran (all
verified against ipstack.js source / in-page probes):

1. **`recv` argument order**: ipstack's signature is `recv(data, offset,
   len)`; the wrapper called `recv(view, view.length, 0)` → 0 bytes, instant
   EOF. Fix: `recv(view, 0, view.length)`.
2. **EAGAIN busy-spin in the writable**: `send` returns -11 when the tx
   buffer is full; the old loop `continue`d synchronously, starving the
   browser event loop so any write larger than the buffer (~10 KB snapshot)
   hung. Fix: `await new Promise(r => setTimeout(r, 5))` on EAGAIN.
3. **Never-resolving `closed` promise**: `closed: new Promise(() => {})`
   never resolved; the core awaits it during guest teardown, so any socket-
   using process could wedge at exit. Fix: resolve `closed` on `close()` and
   on EOF (TCP and UDP wrappers).

Verified with `tests/e2e/{data-path,big-put,stream-put}-probe.mjs` (the
stream probe drives the REAL `network.js` adapter — exposed as
`window.cjTailscaleAdapter` so it cannot silently drift from the wrapper).

### 16.3 Guest sync agent rework (diskimage/sync, desktop.start)

The agent itself was broken by CheerpX process/timer quirks (each verified by
backend log mirrors):

1. A backgrounded `su user -c …` never executes its child — run the agent as
   root, backgrounded like Xorg.
2. The pull process's teardown can wedge the guest — ONE backgrounded root
   process runs pull AND the push loop (`sync-home.sh both`) and never tears
   down.
3. Concurrent guest processes doing overlay FS work can wedge — one process
   avoids it.
4. `HOME` must be explicit (`HOME=/home/user …`) — root's `Path.home()` is
   `/root` and the daemon scanned an empty home.
5. **Every guest wait primitive is unreliable**: `time.sleep()` hangs
   forever; `subprocess.run(["sleep", …])` is flaky; a busy-wait on
   `time.time()` starves the guest clock (it only advances when the wasm
   yields). Fix: the sync's critical path never sleeps (`DEBOUNCE_S = 0`);
   `_sleep()` is a best-effort socket-timeout wait. (This is the "§16 item 5"
   cited by `diskimage/rootfs/usr/lib/python3.14/site-packages/sitecustomize.py`.)
6. A baked macOS `.DS_Store` (6148 B) broke the snapshot PUT (large-body send
   flow control) — removed from the image.
7. `signal.signal()` wrapped in try/except; crash-safety net writes
   `_daemon-error.log` to the backend.

Result: pull (ping → lease → restore) then push loop (lease → initial
snapshot → per-file uploads → heartbeat) with `webvm.lock` + `snapshot.tar.gz`
+ home files on the WebDAV backend.

### 16.4 Test-infrastructure fixes

- Playwright's `APIRequestContext` `auth` does not send Basic auth on plain
  HTTP (silent 401) — explicit `Authorization` header (shared in
  `tests/e2e/lib/webdav-auth.js`); `playwright.config.js` host-resolver rules
  (macOS parity with CI).
- The sync spec's `E2E_SYNC=1` gate **removed**: it runs whenever the webdav
  CI phase provides `E2E_WEBDAV_*` (the browser phase has none and still
  self-skips).

### 16.5 Verified end state

Full Playwright suite on the webdav stack passes (boot ×3, desktop,
persistence ×2, sync); unit 81; browser node online in `headscale nodes
list`. **§16.5's claim that the guest data path worked was INCOMPLETE** —
see §16.8.

### 16.6 Leftovers / notes (current)

- The headscale pin stays at **0.28.0** (works with the rebuilt capver-142
  client; unpinning is one line).
- The diagnostic probes stay in `tests/e2e/` as engineering tools
  (`repro-tailnet.mjs`, `data-path-probe.mjs`, `big-put-probe.mjs`,
  `stream-put-probe.mjs`).
- The reference webvm.io's tailnet remains broken in current Chromium (§15.1)
  — still worth reporting to Leaning; this repo no longer depends on a fix.
- The guest's eth0 NIC is still never created by the core; guest networking
  works through the core's syscall-level socket dispatcher, so nothing in the
  guest needs eth0. `/proc/net/dev` is ABSENT in the guest (upstream core
  gap, documented 2026-08-16) — do not treat its absence as a networking-
  health check; verify via the sync lock/lease on the backend or
  `headscale nodes list`. If it is ever needed (e.g. curriculum code reads
  it), a static `lo` table can be synthesized (DataDevice mounted at
  `/proc/net`); rejected for now.

### 16.7 Review-driven hardening (same session)

Security: `scan_local` skips symlinks (agent runs as root); agent-written
files are chown'ed to the home owner; WebDAV redirects drop Authorization on
cross-scheme/host targets. Privacy/perf: logtail uploads removed from the
rebuilt client; per-notify netmap dump reduced; per-packet drop log
rate-limited. Reliability: TCP/UDP wrappers resolve `closed` on EVERY failure
path; `both` mode crash-nets the PULL phase too; `_sleep` busy-waits the
remainder on native runtimes. Deploy: privileged host port 443 publish moved
to the gateway (tailnet profile only); `fetch-cheerpx-runtime.sh` no longer
clobbers `tun/wasm_exec.js` (the rebuilt pair is committed); the sync spec
sets its own timeout.

### 16.8 Guest data path still dead — fixed by re-running the core's net-init (2026-08-16)

With the app-side driver's autoConf+up ALONE, the guest's `connect(2)` never
completes app-side even though the browser-side netstack finishes the TCP
handshake (45/45 `opened` resolved, ZERO HTTP bytes flow — `nc -z` hangs, no
lease ever lands). **Fix:** after the driver's autoConf+up, call the CORE's
own net-init `window.cheerpOSNetInit(...)` (same tun path + the driver's
callbacks); the second autoConf+up re-establishes the working guest data path
(2/2 manual runs + sync spec's lock assertion; 0/5 without). The core's own
invocation, if it ever runs, is idempotent. Residual flakiness (pre-existing
CheerpX quirks): the boot can occasionally wedge at "Starting local …", and
the boot pull's `wait_for_tailnet` cycles 12 × ~15-20s under the slow guest
clock — sync spec budgets are 240s (lock + snapshot) with a 600s timeout.

### 16.9 Guest bind(2)/listen(2) crashed the core — TCPServerSocket implemented; inbound accept is dead; CONTROL_HOST findings (2026-08-16)

1. **`bind(2)`/`listen(2)` crashed the core** — `nc -z` in the guest ended
   in `Uncaught TypeError: r.TCPServerSocket is not a function`: the core's
   guest-socket dispatcher forwards `connect(2)` to `interface.TCPSocket`
   (case 88) but `bind`/`listen` to `interface.TCPServerSocket` (case 36),
   which the custom `networkInterface` did not implement. BusyBox `nc`
   unconditionally binds before connecting, so even the plain probe crashed
   (the sync agent's implicit bind in `connect(2)` never hits case 36 — why
   §16.8's data path worked). **Fix:** implemented `TCPServerSocket(addr,
   {localPort})` mirroring the runtime's (bind → listen → accept loop over
   `accept()`/`waitIncoming()`, streaming `{opened, closed, close}` wrappers
   via the shared `connectedTcpSocket()` helper, which now also backs
   `TCPSocket`); exposed `window.cjTailscaleCurrentIp` for the E2E
   listen-twin probe. **2026-08-18:** the listen-twin probe hung the whole
   test (600s) because the ReadableStream `pull()` awaited IpStack's
   `waitIncoming()`, which busy-spins when no connection arrives. The wrapper
   now polls `accept()` with a 100 ms yield — bind+listen never blocks the
   main thread.
2. **Inbound TCP for the node's own IP is consumed by the rebuilt
   tailscale.wasm — guest servers can bind+listen but never accept.** A SYN
   from a REAL peer never reaches the tun (the client's own-IP handling
   swallows it: `initPeerAPIListener: 2 netmap addresses match existing
   listeners`; the IpStack spins a SYN/SYNACK retransmission loop). This is a
   RUNTIME limitation, not a bug in our code: no guest LISTEN services
   (sshd, git daemon, `python3 -m http.server`) can ever accept; the E2E
   listen-twin asserts bind+listen ONLY (network.spec.js), and IDLE's
   launcher gates subprocess mode on a loopback round trip (display-bug.md
   §2.11). Outbound guest traffic is unaffected.
3. **`CONTROL_HOST=127.0.0.1` (browser-facing) broke the guest OUTBOUND data
   path** — with `server_url=https://127.0.0.1:8443` the page-side adapter
   probe works but the guest never lands the lease; with the hostname it
   worked. The mechanism was the DERP reachability, not the wasm netmap
   handling: the netmap's DERP host (`127.0.0.1` with server_url=127.0.0.1)
   is the GATEWAY's own loopback, and the gateway could never reach the
   relay. **Fixed by the §16.10 loopback socat relay; §16.9's "revert to the
   hostname" verdict is SUPERSEDED — hostnames are banned.** The §16.9-era
   "unresolved flake" (data path stayed broken across many runs after the
   flip-back, then recovered after a full `make build`; suspected two-client
   heal interplay) was never reproduced with the relay in place — re-check
   if the wasm client is ever rebuilt (§16.1 source).

### 16.10 host.docker.internal REMOVED — IP literals only (2026-08-16, user mandate)

**The user's hard requirement (categorically imperative, never to be
reintroduced):** NO hostnames anywhere — no `host.docker.internal`, no
`/etc/hosts` entries, no custom DNS for LAN users. Everything must work with
`127.0.0.1` (zero-config single machine) and a hardcoded LAN address such as
`192.168.x.x` (LAN) alone.

**Mechanism that makes 127.0.0.1 work (the missing piece in §16.9):** the
netmap's DERP region host is derived from headscale's `server_url`, i.e. the
BROWSER-facing `CONTROL_HOST` — `127.0.0.1` on the single machine. Inside the
GATEWAY container `127.0.0.1` is its OWN loopback, so its tailscaled could
never reach the DERP relay at `https://127.0.0.1:8443/derp` (exactly what
§16.9 observed as "guest data path dies"). The fix is a **loopback socat
relay in the gateway on CONTROL_PORT forwarding to the server's static
compose-network IP** (`start_relay "${CONTROL_PORT}"
"${GATEWAY_CONTROL_IP}:${CONTROL_PORT}"` in gateway/entrypoint.sh). On LAN
deployments the DERP host is the LAN IP, reached directly through the host
(the relay is then unused but harmless).

**Everything hostname-shaped was removed 2026-08-16:**
- `CONTROL_HOST` defaults to `127.0.0.1` in every file (`server/entrypoint.sh`,
  `scripts/print-url.sh`, `scripts/gen-certs.sh`, `scripts/acceptance.sh`,
  `compose.yaml`, `.env.example`) — LAN deployments set it to a hardcoded LAN
  IP. No `/etc/hosts` requirement anywhere.
- The gateway uses `GATEWAY_CONTROL_IP` (default `172.28.0.10`, the server's
  static compose-network IP on the fixed `172.28.0.0/16` network; cert SAN
  covers it) for `--login-server` and its relays; `extra_hosts` removed from
  compose.yaml entirely.
- `scripts/gen-certs.sh` SAN no longer carries `DNS:host.docker.internal`;
  CI no longer edits the runner's `/etc/hosts`; the E2E host-resolver-rules
  were removed; the join-test client joins via `https://172.28.0.10:8443`.
- **Enforcement:** `tests/unit/test_scripts.py::test_control_host_defaults_consistent`
  asserts every CONTROL_HOST default is `127.0.0.1` AND that the literal
  `host.docker.internal` appears in none of the runtime config/scripts/tests/
  CI files (the banned list is in the test). This test FAILS CI if the
  hostname is ever reintroduced — keep it green. AGENTS.md carries the rule.

**Verification status:** the E2E `network.spec.js` root-visit test (baked
config → `webvm.lock` lease → nc-twin socket probe → listen-twin bind+listen)
passes on the single machine with `CONTROL_HOST=127.0.0.1` defaults and no
/etc/hosts entry.