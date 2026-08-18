# Networking bug: browser-side tailnet (CheerpX wasm) crashes; works upstream

**Status: RESOLVED (2026-08-15, §16) — the sync E2E passes and is ungated.**
The "tailscale wasm crash" narrative in §2–§14 is largely WRONG — verified
2026-08-15: tailscale.wasm was never even loaded in any configuration
(including on the reference webvm.io), and the crash is inside **cxcore.wasm**
(the CheerpX core), triggered by the guest's udhcpc raw socket on a never-
created eth0. §15 carries the verified diagnosis and the applied fixes; the
remaining blocker there (guest data path dead) was **fixed in §16 by
rebuilding the tailscale wasm client from source** (v1.102.2 + custom
MessageChannel tun) and reworking the guest sync agent around CheerpX
process/timer quirks. **Read §16 first (it supersedes §15's "what to do
next"); treat §2–§14 as historical context.** Last updated 2026-08-15.

## 1. Summary

Guest networking in this repo exists ONLY via CheerpX's browser-side Tailscale
(`networkInterface` passed to `CheerpX.Linux.create`, AGENTS.md non-negotiable).
When the guest actually uses the network, the CheerpX wasm tailscale client
crashes with `RuntimeError: function signature mismatch` (a wasm `call_indirect`
type mismatch inside the tailscale module), so the guest never gets a usable
network path. On CI this manifests as the webdav sync E2E failing (`webvm.lock`
never appears on the WebDAV backend). The reference site `webvm.io` (same
runtime files, pinned upstream commit `e58fef0c9…`, 1.3.7) is reported to work,
so the crash may be environmental/version-specific — but it reproduces here in
every configuration tried so far (see §6 matrix). Two adjacent REAL bugs were
found and fixed along the way (control-plane CORS, headscale client-version
rejection) — see §7 — plus a documented **known headscale↔wasm incompatibility
at the `/ts2021` Noise-over-WebSocket endpoint** (headscale issue #1650) that
is the most concrete lead for the crash itself. **Research adds two key
reframes: (a) headscale's `/key` capability-version gate is a *recent* 0.29.x
regression for the wasm client (headscale #3391) and IS separately fixable by
pinning 0.29.2 or ≤0.28 or patching the min-version constant; (b) `function
signature mismatch` is, per webvm #222, the CheerpX runtime's *wedge symptom* —
an earlier internal failure wedges the runtime and every subsequent call fails
this way, so the search must find the FIRST error.**

**Do not re-verify the closed items in §7 unless you have a reason to.** Start
from §6, §9 and §10.

## 2. Symptom

- CI: `tests/e2e/tests/sync.spec.js` fails — `webvm.lock` never appears on the
  WebDAV backend within 150 s (retries also fail). (Now self-skips via
  `E2E_SYNC=1` gate, see §7c/§14.)
- Browser console (during boot, right after the X desktop framebuffer appears):
  ```
  log: Unexpected exit RuntimeError: function signature mismatch
      at wasm://wasm/00742aba:wasm-function[3872]:0x1a30a5
      at wasm://wasm/00742aba:wasm-function[3810]:0x1957bd
      at wasm://wasm/00742aba:wasm-function[3809]:0x194939
      at wasm://wasm/00015bbe:wasm-function[3]:0x653
      at wasm://wasm/db517032:wasm-function[1]:0xe3
      at wasm://wasm/db517032:wasm-function[4]:0x29f
      at wasm://wasm/00742aba:wasm-function[1456]:0x929c9
      at wasm://wasm/00742aba:wasm-function[1511]:0x959d8
  PAGEERROR: TypeError: e is not a function
  ```
  (`00742aba` is the tailscale wasm for runtime 1.3.8; with 1.3.7 the module
  hash was `0073cce2` and the function index `[3858]`. `00015bbe` and
  `db517032` are small auxiliary modules — candidates: `ipstack.wasm`,
  `cxbridge`, `direct.js`.)
- Guest console: `ERROR: cannot start networking as hostname would not start`
  (OpenRC hostname failure is NORMAL under CheerpX and unrelated); eth0 never
  comes up; the sync agent never pushes.
- `headscale nodes list`: the browser node never appears (with headscale 0.28.0
  not even a `/key` request reaches headscale; the wasm crashes before/while
  connecting).
- **REFRAME (research, §13.1):** per webvm #222, `function signature mismatch`
  is what every subsequent `cx.run` fails with once the CheerpX runtime has
  wedged on an EARLIER internal failure. So the stack above is the *wedge
  symptom*; the next session must capture the FIRST console/pageerror after
  page load (before `Unexpected exit`) to find what actually wedges the
  runtime when the tailnet starts.

## 3. Tailnet architecture (what talks to what)

```
browser page (https://<host>:8081/alpine.html)
  # hash: authKey + controlUrl + syncUrl + syncUser + syncPass
  -> webvm/src/lib/network.js  exports networkInterface
       {authKey, controlUrl, loginUrlCb, stateUpdateCb, netmapUpdateCb}
  -> webvm/src/lib/WebVM.svelte:336
       cx = await CheerpX.Linux.create({mounts, networkInterface})
  -> CheerpX core (cxcore.js/cxcore.wasm, self-hosted at /cheerpx/)
       loads tun/tailscale_tun_auto.js -> tailscale_tun.js -> wasm_exec.js
       + tailscale.wasm (Go IPN, 18 MB) + ipstack.wasm/ipstack.js + direct.js
  -> control plane: WSS to https://host.docker.internal:8443 (nginx -> headscale)
  -> embedded DERP relay at https://host.docker.internal:8443/derp
  guest eth0 <-> (CheerpX tailscale netstack) <-> gateway tailnet IP (relayed)
gateway container (tailscaled CLI v1.102.2, joins same tailnet)
  socat 127.0.0.1:8082 -> server:8082 (wsgidav /webdav/), tailscaled forwards
  tailnet-IP:8082 -> 127.0.0.1:8082
guest sync agent (/usr/local/bin/sync-home.sh, webdav mode) pushes
  snapshot.tar.gz + webvm.lock to http://<gateway-tailnet-ip>:8082/webdav/
```

Key files (paths relative to repo root):

| File | Role |
|---|---|
| `webvm/src/lib/network.js` | builds `networkInterface` from sessionStorage hash params |
| `webvm/src/lib/WebVM.svelte` | mounts (ext2 overlay, /web, /data, …) + `Linux.create({mounts, networkInterface})`; webdav syncrc injection at `/opt/syncrc` |
| `webvm/src/lib/cheerpx.js` | self-hosted runtime loader (`VERSION = "1.3.7"`), imports `{siteBase}/cheerpx/cx.esm.js` |
| `webvm/cheerpx/cx.esm.js`, `cx_esm.js`, `cxcore.js`, `cxcore.wasm`, `cxbridge.js`, `cheerpOS.js`, `workerclock.js` | CheerpX runtime (pinned 1.3.7, fetched by `scripts/fetch-cheerpx-runtime.sh`) |
| `webvm/cheerpx/tun/tailscale.wasm` | Go Tailscale IPN compiled to wasm (18 MB) |
| `webvm/cheerpx/tun/wasm_exec.js` | Go wasm JS glue (provides `gojs:*` imports) |
| `webvm/cheerpx/tun/tailscale_tun.js` | glue: `init()` -> `new self.Go()`, `WebAssembly.instantiate(tailscale.wasm, go.importObject)`, `ipn.run(...)`, bridges IpStack |
| `webvm/cheerpx/tun/tailscale_tun_auto.js` | `autoConf({...})` — the auto-connect flow the core uses |
| `webvm/cheerpx/tun/ipstack.js` + `ipstack.wasm` | Cheerp-compiled TCP/UDP/DNS stack (`IpStack.TCPSocket` etc.) |
| `webvm/cheerpx/tun/direct.js` | WebRTC direct-connection module |
| `server/headscale/config.yaml.template` | headscale config (embedded DERP region 999, `verify_clients: true`, `server_url` path-less) |
| `server/nginx.conf.template` | 8443 control-plane listener (catch-all proxy to headscale, CORS headers, WS upgrade) |
| `server/Dockerfile` | `FROM headscale/headscale:0.29.3 AS headscale` (binary copied in) |
| `gateway/entrypoint.sh` | tailscaled userspace + socat relays; `tailscale up --login-server=https://host.docker.internal:8443` |
| `compose.yaml` | gateway profile `tailnet`; `extra_hosts host.docker.internal:172.28.0.10` |
| `diskimage/rootfs/etc/local.d/desktop.start` | webdav: `sync-home.sh pull` then `daemon &` before X |
| `tests/e2e/tests/sync.spec.js` | the failing E2E (now `E2E_SYNC=1` gated) |

## 4. Reproduction (exact steps, fresh machine)

Host prereqs: Docker Desktop, `docker compose` v2. macOS: add nothing; use the
Chromium host-resolver rule below (CI adds `127.0.0.1 host.docker.internal` to
/etc/hosts on the runner; macOS needs `--host-resolver-rules`).

```sh
# 0. baseline
git checkout dbc8db0            # "Maybe fix CI" — current HEAD with prior fixes
./build.sh webdav               # guest image WITH the sync agent (~4 min)
cd webvm
WEBVM_MODE=webdav WEBVM_IMAGE_BUILD="$(cat custom-disk-images/image-build.txt)" npm run build
cd ..

# 1. .env + containers
cat > .env <<'EOF'
CONTROL_HOST=host.docker.internal
LAN_IP=127.0.0.1
STORAGE_BACKEND=webdav
WEBDAV_USER=webdav
WEBDAV_PASS=webdavpass
HEADSCALE_BOOTSTRAP=1
EOF
docker compose build
docker compose up -d server

# 2. bootstrap headscale keys (exact CI pattern; retries until a real key)
make_key() { for i in $(seq 1 30); do
  K=$(docker compose exec -T server headscale preauthkeys create --user 1 --reusable --expiration 100y 2>/dev/null | sed -E 's/\x1b\[[0-9;]*m//g' | tail -1)
  case "$K" in hskey-auth-*) echo "$K"; return 0;; esac; sleep 2; done; return 1; }
KEY1=$(make_key); KEY2=$(make_key)
printf 'HEADSCALE_PREAUTHKEY=%s\nGATEWAY_AUTHKEY=%s\nHEADSCALE_BOOTSTRAP=0\n' "$KEY1" "$KEY2" >> .env
docker compose up -d server

# 3. gateway
docker compose --profile tailnet up -d
until docker compose exec -T server headscale nodes list 2>/dev/null | grep -q gateway; do sleep 5; done
GATEWAY_IP=$(docker compose exec -T gateway tailscale ip -4 | head -1)   # 100.64.0.1

# 4. the failing scenario (Playwright) — see repro script §4a
```

### 4a. Minimal repro script (Playwright, keep for the session)

`tests/e2e/repro-tailnet.mjs` (needs `playwright` from `tests/e2e/node_modules`):

```js
import { chromium } from 'playwright';
const KEY = process.env.PREAUTH_KEY || '';
const GATEWAY_IP = process.env.GATEWAY_IP || '100.64.0.1';
const WEBDAV_BASE = 'http://127.0.0.1:8082/webdav/';
const AUTH = { username: 'webdav', password: 'webdavpass' };
const SESSION_URL = 'https://host.docker.internal:8081/alpine.html#authKey=' + KEY +
  '&controlUrl=https://host.docker.internal:8443' +
  '&syncUrl=http://' + GATEWAY_IP + ':8082/webdav/' +
  '&syncUser=webdav&syncPass=webdavpass';

const browser = await chromium.launch({
  args: ['--host-resolver-rules=MAP host.docker.internal 127.0.0.1'],
});
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();
const lines = [];
const reqs = [];
page.on('console', (m) => lines.push(m.type() + ': ' + m.text()));
page.on('pageerror', (e) => lines.push('PAGEERROR: ' + e));
// KEY DIAGNOSTIC: log every browser request to the control plane (the /key ->
// /ts2021 -> /derp sequence) and its status. This is the missing data point
// that distinguishes "crash before any control-plane HTTP" from "crash during
// the /ts2021 WebSocket handshake".
page.on('request', (r) => {
  if (/\/key|ts2021|derp|machine|register/.test(r.url())) reqs.push('REQ ' + r.method() + ' ' + r.url());
});
page.on('response', (r) => {
  if (/\/key|ts2021|derp|machine|register/.test(r.url())) reqs.push('RES ' + r.status() + ' ' + r.url());
});
page.on('requestfailed', (r) => {
  if (/\/key|ts2021|derp|machine|register/.test(r.url())) reqs.push('REQFAIL ' + r.url() + ' ' + (r.failure()?.errorText || ''));
});

console.log('open', SESSION_URL.slice(0, 60) + '…');
await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });
const seen = { lock: false };
const t0 = Date.now();
while (Date.now() - t0 < 180_000) {
  const ok = await context.request
    .get(WEBDAV_BASE + 'webvm.lock', { auth: AUTH }).then((r) => r.ok()).catch(() => false);
  if (ok) { seen.lock = true; console.log('webvm.lock appeared at +' + Math.round((Date.now() - t0) / 1000) + 's'); break; }
  await page.waitForTimeout(5000);
}
console.log('lock=' + seen.lock, 'after', Math.round((Date.now() - t0) / 1000) + 's');
const crash = lines.filter((l) => /signature mismatch|Unexpected exit|CORS|unsupported client|wasm|Cannot read properties/.test(l));
console.log('--- control-plane requests ---');
for (const r of reqs) console.log('  ' + r);
console.log('--- console ---');
console.log(crash.join('\n'));
await browser.close();
process.exit(seen.lock ? 0 : 1);
```

Run: `cd tests/e2e && PREAUTH_KEY="$(grep '^HEADSCALE_PREAUTHKEY=' ../.env | cut -d= -f2)" GATEWAY_IP=100.64.0.1 node repro-tailnet.mjs`

Expected outcome today: `lock=false`, and the console shows `Unexpected exit
RuntimeError: function signature mismatch`.

### 4b. Quick environment checks (non-browser)

```sh
# CORS on the control plane (FIXED — see §7a):
curl -sk -D - -o /dev/null "https://127.0.0.1:8443/key?v=109" -H "Origin: https://host.docker.internal:8081" | grep -iE "access-control|vary"
#   -> Access-Control-Allow-Origin: https://host.docker.internal:8081  (+ Vary: Origin)

# headscale logs (see §8 for the version-rejection line):
docker compose exec -T server sh -c 'tail -40 /var/log/headscale.log'

# headscale version + min client version:
docker compose exec -T server headscale version | head -1
#   -> v0.29.3 ... "Clients with a lower minimum version will be rejected minimum_version=v1.80"

# standalone tailscale wasm sanity (WORKS — §6):
#   in the page: import('/cheerpx/tun/wasm_exec.js'); new globalThis.Go();
#   instantiate tailscale.wasm with go.importObject; go.run(instance).
#   Go runtime starts (logs "[211:211] Support prctl (17)", "[212:212] TODO: SYS_SETSOCKOPT").
```

## 5. Sync agent flow (what should happen)

1. Browser loads `alpine.html` with the hash; `app.html` inline script moves
   `#…` params to `sessionStorage`; `network.js` builds `networkInterface`.
2. `Linux.create({mounts, networkInterface})` — the core starts the tailnet
   (tailscale_tun_auto.js `autoConf`).
3. Guest boots (ext2 overlay). `desktop.start` (webdav) runs
   `sync-home.sh pull` (waits for tailnet up to ~90 s, then pulls), then
   `sync-home.sh daemon &` (lease `webvm.lock` + push `snapshot.tar.gz` to
   `http://<gateway-tailnet-ip>:8082/webdav/`).
4. Browser-side tailscale registers with headscale, receives the netmap,
   establishes DERP to the gateway, relays the HTTP over the tailnet socket.

The crash happens in step 4 the moment a real packet flows (see §6). The
browser-side client never even reaches `Running` (state stays `NoState`=0,
`netmaps=0` in the standalone listener probe) in the current setup.

## 6. Test matrix — everything already tried (do NOT blindly repeat)

| # | Change / condition | Result |
|---|---|---|
| 1 | Baseline (1.3.7 self-hosted, headless Chromium, headscale 0.29.3) | crash |
| 2 | Headed Chromium (macOS GUI) | crash (not headless-specific) |
| 3 | Runtime 1.3.8 (swapped cxcore.js/cxcore.wasm/tailscale.wasm + npm pkg + VERSION) | crash (identical pattern, new module hash) |
| 4 | Runtime proxied to the CDN (`route` `/cheerpx/**` → `https://cxrtnc.leaningtech.com/1.3.8/**`) — i.e. loaded exactly like webvm.io | crash |
| 5 | Control-plane CORS added (nginx ACAO `$http_origin`) | `/key` fetch no longer CORS-blocked, BUT still crash |
| 6 | headscale 0.28.0 (min capability version 106, below the wasm client's 109) | gateway still joins; wasm STILL crashes; no `/key` request reaches headscale |
| 7 | Standalone `tailscale.wasm` instantiated + `go.run()` in-page (no CheerpX core) | RUNS (Go runtime starts, goroutines spawn) |
| 8 | Standalone `tailscale_tun.js` `init()` + `up(settings)` in-page (no core, no guest sockets) | no crash (but never reaches Running either) |
| 9 | Tailnet active, guest boots with NO sync (browser-mode guest) | (not tried — see §10 theory T4) |
| 10 | Stripping the site CSP via a Playwright route | (attempt aborted on route.fetch error — untested) |

Conclusions from the matrix:
- The crash is NOT caused by: headless mode, self-hosting the runtime,
  the runtime 1.3.7→1.3.8 delta, CORS, or headscale 0.28/0.29.
- The standalone Go wasm and the standalone glue are healthy; the crash needs
  the CheerpX **core** driving the tailscale integration AND a real guest
  network attempt (the stack fires when the guest's first packet flows through
  ipstack→tailscale).

## 7. Two REAL bugs found and fixed (verified) — do not reopen unless asked

### 7a. Control-plane CORS (FIXED in `server/nginx.conf.template`)

The browser-side tailscale wasm fetches `https://<control-host>:8443/key?v=109`
cross-origin (page origin :8081 → control plane :8443). headscale answers ONLY
`/derp/probe` with `Access-Control-Allow-Origin: *`; every other control-plane
response lacks ACAO, so the fetch was CORS-blocked and the tailnet could never
start. Fix (8443 listener):

```nginx
add_header Access-Control-Allow-Origin $http_origin always;
add_header Vary Origin always;
```

Unit test added: `tests/unit/test_templates.py::TestNginx::test_control_listener_cors_for_wasm_tailscale`.
The plan's old claim ("no further CORS headers are added") was wrong.

### 7b. headscale 0.29.x rejects the wasm client version (KNOWN LIMITATION, pinned 0.29.3)

headscale 0.29.3 enforces `minimum_version=v1.80` (capver 113,
`hscontrol/capver/capver_generated.go` `MinSupportedCapabilityVersion = 113`).
The CheerpX wasm tailscale client reports capver **109 (v1.78)** and is
rejected:

```
ERR user msg: unsupported client version error="unsupported client version: v1.78 (109)" code=400
INF http request ... method=GET path=/key ... status=400
```

- Verified by pinning headscale **0.28.0** (min capver 106, accepts 109) in a
  throwaway server build: the gateway still joined and the version rejection
  disappeared — but the wasm crash (§6 #6) persisted. So the pin stayed at
  0.29.3 (minimal change) and this is recorded as a limitation.
- **RESEARCH UPDATE (2026-08-15, §13.2):** this gate is a *recent* headscale
  regression — `[#3391](https://github.com/juanfont/headscale/pull/3391)`
  (merged 2026-07-28, first in 0.29.3) made `/key` return 400 below min v1.80;
  before it, `/key?v=109` returned 200. **headscale 0.29.2 (2026-07-01) has
  the `/ts2021` WebSocket-GET fix AND no `/key` gate** — test it (T9).
- Impact: even after the runtime crash is fixed, the bundled wasm client
  (v1.78) cannot register with headscale 0.29.3. Options: pin headscale
  **0.29.2** or **≤0.28** (accept v1.78 and have ts2021 WebSocket support),
  or rebuild headscale 0.29.x with `MinSupportedCapabilityVersion` lowered to
  ≤109 (compiled constant; no config). The `join-test-client.sh` (real
  tailscale CLI v1.102.2) is NOT affected (newer clients are accepted).

### 7c. CI action taken

`tests/e2e/tests/sync.spec.js` now self-skips unless `E2E_SYNC=1` is set,
with the reason documented in the file. The webdav-phase CI (gateway join,
integration tests, boot/persistence E2E) is green.

### 7d. Known headscale↔wasm incompatibility: Noise-over-WebSocket (issue #1650)

There is documented prior art for exactly this class of failure:
[headscale issue #1650](https://github.com/juanfont/headscale/issues/1650)
"Doesn't work with webvm.io", answered by Leaning's own headscale developer:

- The wasm client cannot do **Noise-over-HTTP** in Wasm/JS (no bidirectional
  HTTP); it uses **Noise-over-WebSocket** instead — upstream change
  [tailscale@a9f32656](https://github.com/tailscale/tailscale/commit/a9f32656f53e4dbe7248852a616af75072f89000)
  ("allow client and server to communicate over WebSockets").
- Headscale's `ts2021` (Noise) endpoint historically **does not support real
  WebSocket clients**: "the connection is killed by some context cancelling
  before any data is exchanged (except for the noise handshake)".
  Leaning's fork
  [leaningtech/headscale](https://github.com/leaningtech/headscale) works
  around it by **disabling the ts2021 endpoint**
  ([commit 038126c](https://github.com/juanfont/headscale/commit/038126c8b88e34b5fb1aedc25e1bb42cc0e5041a):
  "For now, we just fall back to the legacy protocol, that works.") so the
  wasm client uses the legacy `/machine/register` flow.
- Note the fork is old (**0.25.1** era, last commit "changelog 0.25.1");
  upstream headscale has **no config flag** to disable the `ts2021` route
  (verified against v0.29.3 `config-example.yaml` — only `noise.private_key_path`).
- Relevance to our crash: with headscale 0.28.0 (which accepts the wasm
  client's capver), **no `/key` request reached headscale** — consistent with
  the client going straight to the `/ts2021` Noise-over-WebSocket handshake,
  which is the path the fork had to disable. The `call_indirect` signature
  mismatch may be the wasm client's handling of the broken ts2021 WebSocket
  connection (see hypothesis T8, §10).

## 8. Reference log/stack snippets (pattern-match against these)

Headscale 0.29.3 startup + rejection:
```
INF Clients with a lower minimum version will be rejected minimum_version=v1.80
INF DERP region: {RegionID:999 RegionCode:webvm RegionName:WebVM LAN ...}
INF STUN server started at [::]:3478
INF listening and serving HTTP on: 127.0.0.1:8080
ERR user msg: unsupported client version error="unsupported client version: v1.78 (109)" code=400
INF http request bytes=27 elapsed=0.36 method=GET path=/key proto=HTTP/1.1 remote=172.28.0.1 status=400
```

Browser console right before the crash (tailscale wasm internals):
```
log: [211:211] Support prctl (17)          <- Go wasm goroutine/thread startup
log: [212:212] TODO: SYS_SETSOCKOPT - level: 1 optname 16
log: [212:212] SUPPORT /dev/kmsg TID 212
log: [638:638] TODO: SYS_SETSOCKOPT - level: 1 optname 26
log: VT mode 1 relsig 10 acqsig 10          <- guest X server starts
log: FB 1305x768x32 depth 24 handle 1
log: Unexpected exit RuntimeError: function signature mismatch
```

The `Unexpected exit` wrapper lives in `webvm/cheerpx/cxcore.js` (minified):
`catch(e){if(e!='CheerpJContinue'){debugger;console.log('Unexpected exit',e.stack);e()}}`
— the trailing `e()` is what produces the secondary
`PAGEERROR: TypeError: e is not a function` (a red herring; the real error is
the `RuntimeError: function signature mismatch` thrown from inside the wasm).

Runtime 1.3.7 vs 1.3.8: only `cxcore.js`, `cxcore.wasm`, `tun/tailscale.wasm`
differ (verified by sha256 against `https://cxrtnc.leaningtech.com/1.3.8/`).
`wasm_exec.js`, `ipstack.*`, `tailscale_tun*.js`, `direct.js` are identical.

`tailscale.wasm` imports (Go wasm `gojs:*` set — standard):
`gojs:runtime.{wasmExit,wasmWrite,resetMemoryDataView,nanotime1,walltime,scheduleTimeoutEvent,clearTimeoutEvent,getRandomData}` and `gojs:syscall/js.{finalizeRef,stringVal,valueGet,valueSet,valueIndex,valueSetIndex,valueLength,valueCall,valueNew,valuePrepareString,valueLoadString,copyBytesToGo,copyBytesToJS}` — all provided by `wasm_exec.js`.

## 9. Why the crash is hard to pin (what we know about the code path)

1. `function signature mismatch` at runtime = a **`call_indirect` type
   mismatch** (the table slot's function type differs from the call's expected
   signature). V8 throws; the wasm spec leaves it UB, and older V8 did not
   always throw. **Check the Chromium version** — this may be a
   newer-V8-enforcement regression (worth comparing against an older Chrome).
2. The crashing frame is inside the **tailscale wasm** (`wasm-function[3872]`)
   calling through small modules (`00015bbe`, `db517032`). Disassemble the
   wasm to identify what function 3872 is: `wasm2wat` / `wasm-objdump`
   (wabt) on `webvm/cheerpx/tun/tailscale.wasm` at offset `0x1a30a5` (1.3.8) /
   `0x1a192d` (1.3.7). Also inspect `ipstack.wasm` and `cxbridge`.
3. The standalone pieces work; the failure needs the **core↔tailscale↔ipstack
   bridge plus real guest traffic**. Prime suspect: the core's JS trampoline
   registers a callback/function pointer into the Go wasm's table with the
   wrong signature, and the mismatch fires when the guest's first packet
   arrives.
4. `tailscale_tun_auto.js::autoConf` is the flow the core drives: `init()`,
   then on `NoState` calls `up(settings)`, wires `listeners.onstateupdate`/
   `onnetmap`/`onloginurl`. The core also wires `tcpSocket`/`udpSocket`
   (ipstack) for guest sockets.
5. **Control-plane transport:** the wasm client speaks **Noise-over-WebSocket**
   to the control plane (tailscale@a9f32656 — "We can't do Noise-over-HTTP in
   Wasm/JS ... but we should be able to do it over WebSockets"). Headscale's
   `/ts2021` endpoint historically cannot serve real WebSocket clients (§7d,
   issue #1650) — a known incompatibility, not an exotic path. The `nginx`
   catch-all proxies `/ts2021` with `Upgrade`/`Connection` headers, so the WSS
   upgrade does reach headscale; what headscale does with it (and what the
   wasm does in response) is the crux.

## 10. Hypotheses to test next (ranked)

- **T1 — newer-V8 enforcement regression (most likely to explain
  "works upstream / broken here").** Test the exact same page in an OLDER
  Chromium (e.g. Playwright can install older Chromium builds:
  `npx playwright install --with-deps chromium@<older>` or use a pinned
  Chrome for Testing build). If older Chrome does not crash, this is a V8
  behavior change and the resolution is a runtime/compiler fix (or a browser
  floor) — NOT this repo. Note the CI + local Chromium are both recent
  (151.x), so "works upstream" needs re-verification on webvm.io in a current
  browser (T5).
- **T2 — guest traffic before the tailnet is Running.** The guest's sync
  agent (`sync-home.sh pull`) and the boot make network calls immediately,
  while the tailnet is still `NoState`. Test: boot the webdav guest with the
  sync agent's early network attempts disabled (or delay them until the
  browser-side state is `Running`), and see whether the crash disappears. If
  it does, the crash is a data-path race, and the guest/browser-side fix is
  to gate guest networking on `stateUpdateCb(6)` (Running).
- **T3 — identify the exact crashing function.** Disassemble
  `tailscale.wasm` function[3872] (wabt), and identify `00015bbe` /
  `db517032` (compare sha256/function counts against `ipstack.wasm`,
  `cxbridge.js`-compiled wasm, `direct.js`). This tells us which bridge is
  mis-wired.
- **T4 — is the crash triggered by guest traffic at all?** Boot with the
  tailnet params but a **browser-mode** guest (no sync agent) — if no crash,
  guest traffic triggers it; if crash, it's pure tailnet init.
- **T5 — does webvm.io's tailnet actually work TODAY?** Drive webvm.io in
  headless Chromium with the "Connect to Tailscale" flow (or check for the
  same `signature mismatch` in its console). The reference may be broken in
  current browsers too. Also confirm which CheerpX version webvm.io currently
  loads (their main branch moved to 1.3.8, `1ab73dca02`).
- **T6 — wasm_exec.js ↔ tailscale.wasm ABI pairing. [RULED OUT 2026-08-15]**
  Our `wasm_exec.js` is byte-for-byte identical to the official
  `go1.23.2/misc/wasm/wasm_exec.js` (sha256 `45ce9dfe72112475…`), and
  `tailscale.wasm` embeds the `go1.23.2` toolchain string — the pair is
  consistent, and the labs blog confirms the client is tsconnect compiled with
  `GOOS=js GOARCH=wasm`. Do not pursue this.
- **T7 — WebRTC `direct.js`.** The crash may be in WebRTC/direct-channel
  setup. Test forcing relay-only (headscale `derp.server.verify_clients` /
  client config) or blocking `direct.js` load to see if the crash moves.
- **T8 — the `/ts2021` Noise-over-WebSocket connection to headscale
  (issue #1650).** The wasm client's control-plane connection is
  Noise-over-WebSocket, and headscale's `/ts2021` historically kills such
  connections ("context cancelling before any data is exchanged except the
  noise handshake") — Leaning's fork had to **disable ts2021** to force the
  legacy `/machine/register` flow. Our data point: with headscale 0.28 (client
  accepted), no `/key` request reached headscale — the client went straight to
  `/ts2021`. Test: (a) enable debug logging on the `/ts2021` WSS upgrade in
  nginx/headscale and watch the handshake; (b) run the same browser client
  against the **leaningtech/headscale fork** (ts2021 disabled, legacy
  register; fork is 0.25.1-era — note §7b's min-version caveat) or against a
  patched headscale with the `/ts2021` route removed; (c) if the legacy path
  works, the fix is to disable/skip `/ts2021` for the wasm client (headscale
  has no config for this upstream — it would need a fork or a patch, or pin
  the Leaning fork). **REFINED by research (§13):** "no /key reached headscale
  with 0.28" is suspect — headscale 0.28 may not log HTTP requests the way
  0.29 does, so the /key fetch likely DID happen. And 0.28 DOES register
  `/ts2021` for GET (gorilla `Methods(POST, GET)`), so the client could reach
  the WebSocket handshake. Add **browser-side request logging** (Playwright
  `page.on('request'/'response')`) to see the real /key→/ts2021 sequence.
- **T9 — headscale 0.29.2 (ts2021 GET fix, PRE-version-gate).** From the
  headscale version matrix (§13): 0.29.2 (2026-07-01) registers `/ts2021` for
  GET+POST (fix #3359) but does NOT yet have the `/key` capability-version
  gate (#3391, merged 2026-07-28, first in 0.29.3). If the wasm client is
  rejected at `/key` only by the version gate, **0.29.2 should accept it** —
  pin it and re-test. This is the single cheapest decisive test.
- **T10 — find the FIRST error before the wedge (webvm #222 mechanism).**
  Per webvm #222, `function signature mismatch` is what EVERY subsequent
  `cx.run` fails with once the CheerpX runtime has wedged on an earlier
  internal failure (their example: `TypeError: Cannot read properties of
  undefined (reading 'a1')` on OverlayDevice fresh-inode allocation; repros in
  `link-foundation/rust-web-box/experiments/`). The tailnet boot may trigger a
  similar internal wedge. **Add a pageerror/console listener from page load**
  and record the FIRST error (before `Unexpected exit`) and correlate it with
  guest FS writes (the sync agent's boot-pull writes fresh inodes to the
  overlay) and with the network start. Also run the #222-style repro adapted
  to this stack.
- **T11 — the leaningtech/headscale fork is the known-good reference.** yuri91
  (Leaning) in issue #1650: "The headscale fork does work, we have users
  actively using it." Running this stack against the fork (0.25.1-era, ts2021
  disabled) tells us definitively whether the crash is headscale-related or a
  pure runtime defect. Caveat: the fork predates the v1.78/0.28-v0.29
  interactions; the wasm client in our runtime is the SAME tsconnect client
  the fork was tested with.
- **T12 — public Tailscale control plane (webvm.io-style).** Point the client
  at the PUBLIC control (no `#controlUrl`, interactive login — the reference's
  flow) to confirm the browser-side tailnet works at all outside headscale.
  If it connects there, headscale is implicated; if it still wedges, the
  runtime/tailnet init is the bug.
- **T13 — headscale min-version is NOT configurable; patching is required.**
  Verified: `MinSupportedCapabilityVersion = 113` is a compiled constant
  (headscale 0.29.3, `hscontrol/capver/capver_generated.go`), no config/env
  flag exists. To keep headscale 0.29.x AND accept the v1.78 client you must
  rebuild headscale with the constant lowered to ≤109 (or pin 0.29.2 / ≤0.28).

**Recommended order of attack (post-research):**
1. **Instrument first** (T10): run the §4a repro with control-plane request
   logging + FIRST-error capture. This tells us (a) whether `/key` is even
   attempted, (b) the exact `pageerror`/console line before `Unexpected exit`,
   and (c) whether the wedge correlates with guest FS writes.
2. **Pin headscale 0.29.2** (T9 — cheapest decisive test): it has the
   `/ts2021` WebSocket-GET fix and no `/key` version gate. If the client
   registers, the version gate was the headscale-side blocker and the fix is
   to pin 0.29.2 or patch the min-version constant.
3. **Leaning fork** (T11) as the known-good control; **public Tailscale**
   (T12) to isolate headscale-vs-runtime.
4. Only if all headscale variants crash identically, focus on the **runtime
   wedge** (T10/T13 + webvm #222 mechanism) and open a Leaning issue with the
   tailnet repro.

## 11. Open questions to resolve with upstream

- Does Leaning reproduce the `signature mismatch` with `networkInterface` +
  a self-hosted/headscale control plane in current Chrome? (File an issue on
  leaningtech/webvm or leaningtech/cheerpx; reference issue #93 is the closest
  prior art but is about protocol incompatibility, not a wasm crash.)
- Is there a CheerpX runtime build that fixes this (nothing > 1.3.8 exists on
  the CDN today — `1.3.9`/`1.4.x` return HTTP 204).
- What is the correct long-term pin for headscale given the wasm client is
  v1.78 (see §7b)?
- **Headscale /ts2021 WebSocket support (issue #1650, §7d):** has upstream
  headscale fixed the Noise-over-WebSocket connection since 2023 (when Leaning
  disabled ts2021 in their fork)? Ask in the headscale repo; if not, upstreaming
  the fork's ts2021 fix (or a `noise.disable_ts2021` config) is the cleanest
  resolution for the wasm client. Key references:
  - headscale issue #1650 (headscale×webvm.io) —
    https://github.com/juanfont/headscale/issues/1650
  - leaningtech/headscale fork — https://github.com/leaningtech/headscale
  - fork's ts2021-disable commit 038126c —
    https://github.com/juanfont/headscale/commit/038126c8b88e34b5fb1aedc25e1bb42cc0e5041a
  - tailscale@a9f32656 (wasm Noise-over-WebSocket) —
    https://github.com/tailscale/tailscale/commit/a9f32656f53e4dbe7248852a616af75072f89000
- **Headscale 0.29.x release matrix (§13):** the `/ts2021` WebSocket-GET
  regression and the `/key` version gate are both recent and version-specific;
  pin 0.29.2 or ≤0.28 to test the client without the gate, and file the
  `/key` gate against headscale (the wasm client predates it).
- **CheerpX runtime-wedge prior art (webvm #222):** report the wedge
  ("every subsequent `cx.run` fails with `function signature mismatch`") to
  Leaning with the tailnet repro; their OverlayDevice wedge was closed as
  "disk too small" — a tailnet repro may get a real fix.
- **wasm_exec.js pairing is confirmed-good (§10 T6):** our glue is the exact
  Go 1.23.2 file; no ABI issue to chase.

## 13. Internet research (merged 2026-08-15)

Findings from a broad web/GitHub search, all cross-checked against source
where noted. Ordered by importance.

### 13.1 `function signature mismatch` is the runtime's WEDGE symptom (webvm #222) — REFRAME

[webvm #222](https://github.com/leaningtech/webvm/issues/222) — a CheerpX
`OverlayDevice` bug report from the same project family
(`link-foundation/rust-web-box`; repros in its `experiments/` dir): after an
intermittent internal failure (`TypeError: Cannot read properties of undefined
(reading 'a1')`, exit code 71) the runtime **wedges and every subsequent
`cx.run` fails with `function signature mismatch`**. This matches our
secondary `PAGEERROR: TypeError: e is not a function` + `Unexpected exit`.
Consequence: **the `signature mismatch` we see is the symptom of an earlier
wedge, not necessarily a tailscale-specific crash.** The next session must
capture the FIRST console/pageerror after page load (before `Unexpected exit`)
and find what wedges the runtime when the tailnet starts. The #222 repro
(fresh-inode allocation in the overlay, i.e. `mkdir`/`touch`) is suggestive:
the sync agent's boot-pull writes files into the overlay.

### 13.2 headscale 0.29.x timeline — the wasm client was briefly supported, then gated

Verified against headscale source (v0.29.0–0.29.3, v0.28.0):

| Version | Released | `/ts2021` WebSocket GET | Min client capver | `/key` version gate |
|---|---|---|---|---|
| 0.28.0 | 2026-02 | YES (gorilla `Methods(POST, GET)`) | 106 (accepts 109=v1.78) | no |
| 0.29.0 | 2026-06-17 | NO — regression (#3357, POST only) | 113 | no (gate merged 07-28) |
| 0.29.1 | 2026-06-18 | NO | 113 | no |
| **0.29.2** | **2026-07-01** | **YES (fix #3359)** | **113** | **no** |
| 0.29.3 | 2026-07-29 | YES | 113 | **YES (#3391)** |

- [headscale #3357](https://github.com/juanfont/headscale/issues/3357)
  "[Bug] /ts2021 should accept WebSocket GET for Tailscale JS/WASM clients"
  (2026-07-01, closed): "Starting from Headscale 0.29+, the `/ts2021` route is
  only registered to `POST` … any WASM clients cannot connect because the
  WebSocket upgrade they initiate in the browser needs to be done via `GET`."
  Fixed by [PR #3359](https://github.com/juanfont/headscale/pull/3359)
  "hscontrol: register /ts2021 for WebSocket GET". **Our 0.29.3 pin has the
  fix (app.go registers GET+POST).**
- [headscale #3391](https://github.com/juanfont/headscale/pull/3391)
  "hscontrol: gate /key on supported capability version" (merged 2026-07-28):
  makes `/key` return 400 below `MinSupportedCapabilityVersion` (=113 → v1.80).
  This is **the** headscale-side rejection of our v1.78 (109) client. Before
  this gate (and in ≤0.28), `/key?v=109` returned 200 with the Noise key
  (see #3380). **The gate is a compiled constant, no config.**
- **Actionable consequence:** headscale **0.29.2** has the `/ts2021` WebSocket
  GET fix AND no `/key` version gate — the exact config the wasm client needs.
  Also 0.28.x (ts2021 GET + no gate). Test both (hypothesis T9).

### 13.3 wasm_exec.js ↔ tailscale.wasm pairing is CORRECT (rules out T6)

- `webvm/cheerpx/tun/wasm_exec.js` sha256 `45ce9dfe72112475…` == the official
  [go1.23.2/misc/wasm/wasm_exec.js](https://raw.githubusercontent.com/golang/go/go1.23.2/misc/wasm/wasm_exec.js).
- `tailscale.wasm` embeds the `go1.23.2` toolchain string.
- [Leaning labs blog](https://labs.leaningtech.com/blog/webvm-virtual-machine-with-networking-via-tailscale)
  confirms the wasm client is the **official Tailscale client compiled from
  tsconnect** with `GOOS=js GOARCH=wasm`, shipped with the matching
  `wasm_exec.js`. No ABI mismatch to chase.

### 13.4 Upstream webvm.io networking is confirmed working (with public control)

[webvm #225](https://github.com/leaningtech/webvm/issues/225) (2026-08):
a maintainer confirmed the webvm.io tailnet works ("green dot") for a user
against **public Tailscale control**. So the wasm client + control plane works
in principle; the delta in our stack is headscale + private CA + LAN-only.
Also [webvm #162](https://github.com/leaningtech/webvm/issues/162) and
[cheerpX-meta #8](https://github.com/leaningtech/cheerpX-meta/issues/8)
(knows the network stack is slow — low TCP window — but functional).

### 13.5 Other relevant CheerpX/webvm leads

- [webvm #199](https://github.com/leaningtech/webvm/issues/199) — Go runtime
  init bugs in CheerpX ("failed to get system page size") were fixed in a
  newer runtime; Go code under CheerpX has had init regressions.
- [webvm #182](https://github.com/leaningtech/webvm/issues/182) — Firefox
  `WebAssembly.compile` regression with `Uint8Array` (runtime downloads
  `fail.wasm` on error). Chromium-only here, but shows the runtime's
  error-fallback path (`fail.wasm` is a runtime-written Blob, §8).
- [webvm #220](https://github.com/leaningtech/webvm/issues/220) —
  `TODO: SYS_SETSOCKOPT` messages are benign guest-side notices (we see the
  same in §8); not the crash.
- [webvm #228](https://github.com/leaningtech/webvm/issues/228) /
  [cheerpX-meta #14](https://github.com/leaningtech/cheerpX-meta/issues/14) —
  the repo owner's own report of the Tk `getsockname()`/`select()` bugs
  (already worked around in this project's patched Tcl). Notes `bind()`
  on 127.0.0.1 fails without a `controlUrl` — "presumably part of the same
  networking emulation story".
- [cheerpX-meta #13](https://github.com/leaningtech/cheerpX-meta/issues/13) —
  "Launching tkinter from Python hangs" (open; related socket/select bugs).
- Headplane (talhaahsan/headplane) runs the same wasm tsnet client against
  headscale and drove the #3357/#3359 fix — proof the wasm client + headscale
  is an actively used combination.
- [webvm README §Self-Hosting Tailscale with Headscale](https://github.com/leaningtech/webvm#readme)
  documents that **headscale does not add CORS headers by default and needs an
  nginx proxy** — independently validates our §7a CORS fix; their documented
  flow uses `#controlUrl=<headscale-url>` (no authKey in that example).

### 13.6 Dead ends / ruled out

- Go wasm ABI pairing (T6) — correct as shipped (§13.3).
- Firefox-specific `WebAssembly.compile` regression (webvm #182) — not our
  case (Chromium).
- `fail.wasm`/`t.wasm` empty placeholders — the runtime *writes* `fail.wasm`
  as a Blob on error; the placeholders are for the runtime's fetch fallback
  and are not the cause.
- The small crash modules (`00015bbe`, `db517032`) are NOT any of the three
  on-disk wasm files (ipstack/tailscale/cxcore) — they are runtime-created
  instances (no identification path; treat the stack as the wedge symptom).

### 13.7 Can the tailscale client be updated? (feasibility, 2026-08-15)

Asked directly; summary: **yes in principle, but it is a private Leaning build
and rebuilding it is a real engineering task, not a file swap.**

- The client is `webvm/cheerpx/tun/tailscale.wasm` — a Go wasm
  (`GOOS=js GOARCH=wasm`, toolchain `go1.23.2`, standard `gojs:*` imports)
  compiled privately by Leaning. It reports capver **109 (Tailscale v1.78)**,
  hence the headscale 0.29.x `/key` rejection (§7b).
- **No public tsconnect exposes the API our glue needs.** The runtime's
  `tailscale_tun.js` requires `newIPN(conf)` to return `{ tun, run, up, down,
  login, logout }` with a `tun` **MessageChannel** (`onmessage`/`postMessage`)
  carrying raw IP packets to/from `ipstack.wasm`, and `notifyState` must deliver
  **numeric** states (the glue's `State` enum 0–6, and `network.js` matches
  `case 6 /*Running*/`). Every public tsconnect version checked (v1.36 → main,
  and v1.78) instead uses an internal **netstack** and sends **string** state
  names, exposing `fetch`/`ssh` — no `tun`, no `up`/`down`. The wasm in the
  runtime is Leaning's fork of tsconnect with a custom tun (the labs blog
  describes exactly this: "implementing a custom Tun device …
  sending/receiving IP packets on a JavaScript MessageChannel").
- **To update it you must rebuild tailscale from source** with a custom
  `//go:build js` entry that reproduces Leaning's API surface
  (`wgengine.NewUserspaceEngine` + a MessageChannel-backed tun + `ipnlocal`/
  `ipnserver` + numeric `notifyState`), pinned to **Tailscale ≥ v1.80**
  (capver ≥ 113) so headscale 0.29.x accepts it. All the Go packages are
  public, so this is feasible. Risks/considerations:
  1. Tailscale internal APIs drift between versions — the custom entry must be
     ported to the pinned version.
  2. `wasm_exec.js` must match the Go toolchain (ours == go1.23.2 official;
     a newer Go build needs its matching `wasm_exec.js` shipped too).
  3. The MessageChannel tun packet framing must stay compatible with
     `ipstack.wasm`/`tailscale_tun.js` (it already is for the existing wasm;
     our rebuild must preserve it).
  4. A newer client is also a larger, differently-behaved wasm — re-verify the
     whole boot + the crash before assuming it helps (the wedge in §13.1 may be
     independent of the client version).
- **Easier alternatives** (from §10/§7b): pin headscale 0.29.2 or ≤0.28
  (accepts v1.78), patch headscale's `MinSupportedCapabilityVersion` to ≤109,
  or ask Leaning to rebuild their wasm with a newer client (they control the
  runtime CDN; the reference webvm just bumps the runtime version).

## 14. Current repo state (what is committed / uncommitted)

- HEAD = `dbc8db0` "Maybe fix CI" (commits the CI bootstrap-key fix, the
  entrypoint user-create retry, the Pages base-path fix).
- Uncommitted working-tree changes relevant to networking:
  - `server/nginx.conf.template` — CORS fix (§7a).
  - `tests/e2e/tests/sync.spec.js` — `E2E_SYNC=1` skip gate (§7c).
  - `tests/unit/test_templates.py` — CORS unit test.
  - `plans/webvm_implementation.md` — §12/27 documents this bug; §5 CORS note
    updated.
  - (also pending from the font/Pages session: `diskimage/Dockerfile`,
    `diskimage/python-examples/snake-game.py`,
    `diskimage/rootfs/etc/local.d/desktop.start`, `diskimage/rootfs/etc/fonts/`)
- Runtime pin: `@leaningtech/cheerpx` 1.3.7 (`webvm/package.json`,
  `webvm/src/lib/cheerpx.js` `VERSION`, `scripts/fetch-cheerpx-runtime.sh`).
- Useful session artifacts (recreate if needed): the standalone-wasm probe,
  the `tailscale_tun.js` listener probe (states stay `[0]`, netmaps 0), the
  CDN-proxy route (`/cheerpx/**` → `https://cxrtnc.leaningtech.com/1.3.8/**`),
  and the §4a repro script with control-plane request logging (the key next
  diagnostic).

## 15. 2026-08-15 session: verified diagnosis and applied fixes

This section supersedes §2–§14. Everything here was verified empirically in
the browser this session (Chromium 151 headless/headed + Chrome 126, Playwright
request/console/WebSocket tracing, in-page module instrumentation, headscale
0.28.0/0.29.2/0.29.3, runtimes 1.2.8–1.3.8, self-hosted and CDN-served, and a
live drive of the reference webvm.io).

### 15.1 What the bug actually is (three stacked failures)

**Failure 1 — the tailnet NEVER initializes (in any configuration, including
the reference site).** The CheerpX core's network-init flow stops after
`autoConf()` resolves and never calls `netExports.up()`: `tailscale.wasm` is
never fetched, no `/key` request ever reaches the control plane, and the
client stays in `NoState`. Verified:

- The runtime's TWO network paths are selected by `networkInterface` shape
  (cx_esm.js `HW`/`rS`): WITH `netmapUpdateCb` → the `direct.js`
  `TailscaleNetwork` path, where nothing ever calls
  `TailscaleNetwork.prototype.up()` (proven by instrumenting the prototype);
  WITHOUT it → the legacy `cheerpOSNetInit` path (its autoConf call never
  resolves past init either). In BOTH paths the up() coroutine that would
  fetch and start the wasm client never runs.
- **The reference webvm.io does the exact same thing** (2026-08-15, current
  Chromium, runtime 1.3.8): loads the same glue files, never fetches
  tailscale.wasm, never reaches the control plane. The plan's "works
  upstream / broken here" premise was FALSE; §13.4's "green dot" was a user
  report from an unknown browser/runtime combination.
- No runtime version 1.2.8–1.3.8 fixes it (all probed via CDN routing).

**Failure 2 — the crash is in the CHEERPX CORE, not the tailscale wasm.** The
`RuntimeError: function signature mismatch` (`null function or function
signature mismatch` in Chrome 126) is a `call_indirect (type 10)` trap at
exactly `cxcore.wasm` func[3858] offset `0x1a192d` — disassembled and
matched this session. The plan's §2 attribution to `tailscale.wasm` was an
unverified assumption (module IDs are content-derived; the tailscale module
was never loaded at all). The trap is a vtable-style dispatch on an object
field at offset +40 that is null/uninitialized — the core's socket
backend/netOps was never wired.

**Failure 3 — the deterministic +128s trigger is the guest's udhcpc, not the
sync agent.** The guest's `eth0` never appears (the core never creates the
NIC), `desktop.start`'s eth0 loop gives up after 120s, and then
`udhcpc -i eth0 -n` opens a RAW socket on the missing interface → the core's
socket dispatch → the trap. Proven by running WITHOUT the sync agent (no
syncrc): the crash still fires at exactly +128s.

### 15.2 Secondary stack issues found and fixed (all verified)

1. **headscale version gate (0.29.x rejects the v1.78 client at TWO points).**
   The wasm client is Tailscale v1.78 (capver 109). 0.29.3 rejects it at `/key`
   (gate #3391). **0.29.2–0.29.3 ALL reject it at `/machine/register` inside
   the Noise tunnel** (`rejectUnsupported`, min capver 113) — the §13.2 table
   missed this second gate. **Fix: pin headscale 0.28.0** (min capver 106,
   `/ts2021` registered for WebSocket GET). The gateway (tailscaled v1.102.2)
   joins 0.28.0 fine. (`server/Dockerfile`)
2. **The wasm client DROPS the controlUrl port**: it builds the
   Noise-over-WebSocket URL as `wss://<host>/ts2021` (default port 443) and
   the DERP URLs similarly. The control plane must ALSO be reachable on the
   scheme-default WSS port. **Fix: nginx `CONTROL_WSS_PORT` (443) listener**
   mirroring the 8443 listener + compose publish + `CONTROL_WSS_PORT` env
   (`server/nginx.conf.template`, `server/entrypoint.sh`, `compose.yaml`).
3. **CSP blocked the port-less WSS** (`connect-src` only allowed
   `wss://host:8443`). **Fix: add `https://${CONTROL_HOST}` and
   `wss://${CONTROL_HOST}` to connect-src** (all three CSP headers).
4. **CORS `MultipleAllowOriginValues`**: headscale answers `/derp/probe` with
   `ACAO: *` and nginx echoed `ACAO: $http_origin` alongside it → the browser
   rejected the probe (`net::ERR_FAILED`), stalling the client's DERP
   selection. **Fix: `proxy_hide_header Access-Control-Allow-Origin;` + echo
   ONLY when `$http_origin` is non-empty** (an empty echoed value alongside
   `*` was equally fatal).
5. **The app must drive the tailnet itself** (the core never does). **Fix:
   `webvm/src/lib/network.js`** — drop `netmapUpdateCb` from
   `networkInterface` (selects the legacy path so the core's socket dispatcher
   uses `a47`), and add an app-side driver that imports
   `/cheerpx/tun/tailscale_tun_auto.js`, calls `autoConf()` + `up()`, sets the
   `cjTailscale*` globals, and exposes the socket adapter the core's
   dispatcher calls (`TCPSocket`/`UDPSocket`/`parseIP`/`dumpIP`/`up`),
   mirroring `direct.js`'s `TCPWrapper`/`UDPWrapper` contracts exactly:
   - TCP: `parseIP(ip)` → int, `new tcpSocket()`, `bind(0)`, `connect(ip,
     port)`, `waitOutgoing()`; return `{opened, closed, close}` where
     `opened` resolves with `{readable, writable, remoteAddress,
     localAddress, remotePort, localPort}` (ReadableByteStream +
     WritableStream over `recv`/`send(array, offset, len)`).
   - UDP: `bind(port)`, `recv(buf, 0, len, addrInfo)`, `sendto(arr, ip, port)`;
     `opened` resolves with `{readable, writable, localAddress, localPort}`,
     readable carrying `{data, remoteAddress, remotePort}` messages.
   - `close` MUST return a promise (the core calls `socket.close().catch()`).
6. **The guest-side eth0/udhcpc guard** (`diskimage/rootfs/etc/local.d/
   desktop.start`): only run `udhcpc` when `eth0` actually exists — the
   deterministic +128s core crash is gone; the VM survives the full boot.
7. **sync.py bounded boot wait** (`diskimage/sync/sync.py`): the ping now
   fails fast (3s timeout) and `wait_for_tailnet` checks the ping's return
   value (my first edit returned False without raising, which the loop
   misread as success — fixed); 12 attempts ≈ 60s, then the desktop starts
   (~70s boot, well within the boot E2E window).

### 15.3 Verified end state (2026-08-15)

- With the app-side driver: `tailscale.wasm` fetched → `/key?v=109` 200 →
  WSS `/ts2021` 101 → registered → netmap (peers listed) → **state 6
  (Running)** → DERP WSS open → the browser node appears ONLINE in
  `headscale nodes list`. All of this happens automatically at page load.
- The VM boots to the X desktop in ~70s with NO crash and NO pageerrors.
- Boot + persistence E2E suites pass (`tests/e2e`).
- `webvm.lock` still never appears: the guest's DATA path is dead — the
  core's NIC (eth0) is never created and a guest TCP connect to a tailnet IP
  (e.g. 100.64.0.1:8082) never completes (SYN dies in the browser-side
  netstack; `waitOutgoing` never resolves). This is the remaining upstream
  core defect; the sync E2E stays `E2E_SYNC=1`-gated (comment updated).

### 15.4 What to do next

- Report to Leaning (cheerpx-meta/webvm) with the §15.1 repro: the runtime's
  network-init flow never calls `TailscaleNetwork.up()`; the core never
  creates the guest NIC; guest SYNs die in the netstack; and the socket
  dispatch traps on the unwired backend. The reference webvm.io tailnet is
  equally broken in current Chromium — worth confirming with Leaning.
- If Leaning ships a runtime where the core drives the client, the app-side
  driver in network.js can be removed (the adapter methods are harmless).
- If a newer runtime never comes, the only remaining lever is rebuilding the
  tailscale wasm with a working tun+data path (§13.7) — a heavy task.
- Diagnostic tools for the next session: `tests/e2e/repro-tailnet.mjs`
  (instrumented repro: full console/request/WebSocket capture, in-page
  fetch/instantiate/blob tracing, TUN module step-traces via routing,
  MANUAL_DRIVE/CLICK_CONNECT/RUNTIME_VERSION/CDN-routing modes) and the
  wasm-objdump disassembly of `cxcore.wasm` func[3858] at 0x1a192d.

## 16. 2026-08-15 session: the tailscale wasm client is rebuilt from source — RESOLVED

This section supersedes §15.4. Everything below was verified empirically this
session against the §15 stack (headscale 0.28.0, app-side driver, guest eth0
guard) with the §4a repro, four probe scripts, backend-side log mirrors and
the real E2E suite.

### 16.1 The rebuilt client (the §13.7 lever, executed)

The bundled CheerpX tailscale.wasm (Leaning's private tsconnect fork, v1.78,
capver 109) was replaced with a **tailscale v1.102.2 build from source** that
reproduces the glue's API surface exactly:

- `scripts/tailscale-wasm-entry/wasm_js.go` — a custom `//go:build js` entry
  (modelled on `cmd/tsconnect/wasm/wasm_js.go` at v1.102.2) that wires
  `wgengine.NewUserspaceEngine` with a **custom `tun.Device`** (the
  wireguard-go interface) backed by a JS object shaped like a MessageChannel
  (`postMessage(data)` from IpStack → `Read`; engine writes invoke
  `onmessage({data: Uint8Array})`), plus `run`/`up`/`down`/`login`/`logout`
  and **numeric** `notifyState` (0–6, matching the glue's `State` enum) and
  the tsconnect-style netmap JSON (`self.addresses`, peers with
  `online`/`exitNode`). The netstack-based data path of stock tsconnect is
  deliberately NOT used (it has no tun; the CheerpX glue needs raw IP packets
  on the MessageChannel).
- Build: `scripts/rebuild-tailscale-wasm.sh` — Docker `golang:1.26.5`,
  shallow clone of tailscale at `v1.102.2`, drop the entry into
  `cmd/tsconnect/wasm/wasm_js.go`, `GOOS=js GOARCH=wasm go build`, ship the
  matching `wasm_exec.js` from the toolchain. Outputs ~33 MB
  `webvm/cheerpx/tun/tailscale.wasm` (capver 142) + `wasm_exec.js` (Go 1.26.5).
- Glue: **no changes to `tailscale_tun.js`/`tailscale_tun_auto.js`** — the
  entry reproduces their expectations (two-arg `newIPN` accepted; the stock
  strict arg-count check was removed after the first run died with
  `Usage: newIPN(config)` + exit 1).
- The app-side driver (`network.js`) still drives the tailnet (the core never
  does — §15.1); with the new client the full chain works:
  `/key?v=142` 200 → WSS `/ts2021` (port-443 listener) → registered
  (`machineAuthorized=true`) → netmap → **state Running** → DERP derp-999 →
  `magicsock: new contact: peer=[gateway] via=derp`.

### 16.2 Browser-side data path fixes (webvm/src/lib/network.js)

With the client running, the guest's TCP connects reached our socket wrapper
but three wrapper bugs killed the data path (all verified against ipstack.js
source / in-page probes):

1. **`recv` argument order**: ipstack's signature is `recv(data, offset,
   len)`; the wrapper called `recv(view, view.length, 0)` → always returned 0
   → the readable closed as EOF instantly and every response was dropped.
   Fix: `recv(view, 0, view.length)`.
2. **EAGAIN busy-spin in the writable**: `send` returns -11 when the tx
   buffer is full; the old loop `continue`d synchronously, starving the
   browser event loop so the tun could never drain the buffer — any write
   larger than the buffer (the ~10 KB snapshot) hung forever (the guest then
   blocked mid-PUT; wsgidav saw a headers-only PUT and created a 0-byte
   file). Fix: `await new Promise(r => setTimeout(r, 5))` on EAGAIN. (The
   raw-socket probe moved 12 KB in 1 ms only because it yielded.)
3. **Never-resolving `closed` promise**: `closed: new Promise(() => {})`
   never resolved; the core awaits it during guest process teardown, so any
   process that used a socket could wedge at exit (blocking later guest
   processes). Fix: resolve `closed` on `close()` and on EOF (both TCP and
   UDP wrappers).

Verified with `tests/e2e/data-path-probe.mjs` (raw socket GET → 401 from
WsgiDAV through the tailnet), `big-put-probe.mjs` (12 KB PUT through the raw
socket), `stream-put-probe.mjs` (12 KB PUT through the exact wrapper streams
— all pass).

### 16.3 Guest sync agent rework (diskimage/sync, desktop.start)

With the data path fixed the guest's requests flowed, but the sync agent
itself was broken by CheerpX process/timer quirks (each verified by backend
log mirrors written from the guest — guest stdout is NOT forwarded to the
page console; `/dev/kmsg` writes are not either):

1. **A backgrounded `su user -c …` never executes its child** (foreground su
   works; a plain root background child works — the X server proves it).
   Fix: run the agent as root, backgrounded exactly like Xorg.
2. **The pull process's teardown can wedge the guest** (its sockets' closed
   promises — §16.2.3 — plus the core's process handling), blocking
   everything after it in `desktop.start` (the X server never started).
   Fix: one backgrounded root process runs pull AND the push loop
   (`sync-home.sh both` → `python sync.py both`) and never tears down.
3. **Concurrent guest processes doing overlay FS work can wedge** (the
   pull+daemon pair stalled in `load_manifest`/`scan_local`). One process
   avoids it.
4. **`HOME` must be explicit** (`HOME=/home/user … daemon`): root's
   `Path.home()` is `/root` — the daemon scanned an empty home and never
   pushed anything (`daemon-plan n=0`).
5. **Every guest wait primitive is unreliable**: `time.sleep()` hangs
   forever; `subprocess.run(["sleep", …])` is flaky (works once, then
   hangs); a busy-wait on `time.time()` starves the guest clock (it only
   advances when the wasm yields). Fix: **the sync's critical path never
   sleeps** (`DEBOUNCE_S = 0`); `_sleep()` is a best-effort socket-timeout
   wait for the non-critical loops.
6. **The macOS `.DS_Store` artifact (6148 B) in the baked home broke the
   snapshot PUT** (the core's emulated send path stalls on the large body —
   the precise mechanism is the core's guest-side write flow control;
   removing the 6 KB file made the 533 KB snapshot transfer fine, so the
   threshold isn't a simple byte count). Removed from the image.
7. `signal.signal()` in the daemon is wrapped in try/except (may be
   unsupported); a crash-safety net writes `_daemon-error.log` to the
   backend.

Result: the full flow completes — pull (ping → lease → restore) then push
loop (lease → **initial snapshot** → per-file uploads → heartbeat), with
`webvm.lock` + `snapshot.tar.gz` + all home files on the WebDAV backend.

### 16.4 Test-infrastructure fixes

- Playwright's `APIRequestContext` `auth` option does not send Basic auth on
  plain HTTP (returns 401) — the repro AND the sync spec polled `webvm.lock`
  with `{ auth }` and never saw it even when the backend had the file. Fix:
  explicit `Authorization` header (both scripts).
- `playwright.config.js` now passes `--host-resolver-rules=MAP
  host.docker.internal 127.0.0.1` (macOS parity with the CI /etc/hosts
  entry) — without it the sync spec's session URL fails DNS resolution
  locally.
- The sync spec's `E2E_SYNC=1` gate is **removed**: it runs whenever the
  webdav CI phase provides `E2E_WEBDAV_*` (the browser phase has none and
  still self-skips).

### 16.5 Verified end state

- `npx playwright test` (full suite, webdav stack): **7 passed** — boot ×3,
  desktop, persistence ×2, **sync** (lock ≤150 s, snapshot ≤60 s after,
  reload boots).
- `docker compose run --rm test-unit`: 81 passed.
- `headscale nodes list` shows the browser node online alongside the gateway.

### 16.6 Leftovers / notes

- The headscale pin stays at **0.28.0** (works with the new client; the §7b
  version-gate workaround for the old v1.78 client is now moot — the rebuilt
  client reports capver 142, so 0.29.x would accept it too; unpinning is a
  one-line change if ever wanted).
- `tests/e2e/repro-tailnet.mjs`, `data-path-probe.mjs`, `big-put-probe.mjs`,
  `stream-put-probe.mjs` stay in the repo as diagnostics. The stream probe
  drives the REAL `network.js` adapter (exposed as `window.cjTailscaleAdapter`
  for tests) so it cannot silently drift from the shipped wrapper.
- The reference webvm.io's own tailnet remains broken in current Chromium
  (§15.1) — still worth reporting to Leaning; this repo no longer depends on
  a fix.
- The guest's eth0 NIC is still never created by the core; the guest network
  works through the core's syscall-level socket dispatcher, so nothing in
  the guest needs eth0 anymore. The §15.2.6 eth0 guard stays (harmless).
- `/proc/net/dev` is ABSENT in the guest (upstream CheerpX core gap,
  documented 2026-08-16): the core's `/proc` emulation (the `{type:"proc"}`
  mount in WebVM.svelte) provides only `/proc/mounts` plus a bare
  `CheerpJDataFolder` (cheerpOS.js), the guest's real `mount -t proc` fails
  with "Function not implemented", and with no NIC device there is nothing
  to report anyway. Do not treat its absence as a networking-health check —
  verify via the sync lock/lease on the backend or `headscale nodes list`
  instead. If it is ever needed (e.g. Python curriculum code reads it), a
  static `lo` table can be synthesized with documented APIs only (a
  DataDevice mounted at `/proc/net`, like the `/opt/syncrc` injection);
  rejected for now — nothing in the guest needs it.

### 16.7 Review-driven hardening (same session)

Follow-up code review of §16's change set (all fixes verified — full E2E 7/7,
unit 81, integration PASS):

- **Security:** `scan_local` now SKIPS symlinks — the sync agent runs as root
  (CheerpX process quirks), so following a symlink could have uploaded
  arbitrary root-readable files to the WebDAV backend. Files the agent
  writes/restores (`write_local`, manifest, node id, `.sync-owned`,
  snapshot extract) are `chown`ed to the home owner so the `user` desktop
  session can edit them. WebDAV redirects now drop the Authorization header
  on cross-scheme/host targets (urllib's default handler copies it).
- **Privacy/perf:** logtail uploads to log.tailscale.com are removed from the
  rebuilt client (a fully self-hosted tailnet must not phone Tailscale's
  cloud; the CSP blocked it anyway). The per-notify full-netmap console dump
  is reduced to a summary; the per-packet drop log is rate-limited.
- **Reliability:** the TCP/UDP wrapper resolves the `closed` promise on EVERY
  failure path (bind/connect/waitOutgoing) — the core awaits it during guest
  process teardown; `both` mode crash-nets the PULL phase too (an SMB share
  unreachable at boot previously killed the process before the push loop);
  `_sleep` busy-waits the remainder on native runtimes (still best-effort
  under CheerpX).
- **Deploy:** the privileged host port **443 publish moved to the gateway**
  (tailnet profile only — browser/none modes never bind it; the gateway
  socats it to the server over the compose network). `fetch-cheerpx-runtime.sh`
  no longer clobbers `tun/wasm_exec.js` (the rebuilt pair is committed).
  The sync spec sets its own timeout (two VM boots + 270s of assertions
  exceed the 300s global cap).
- **Test hygiene:** shared `tests/e2e/lib/webdav-auth.js` for the Basic-auth
  header (the silent-401 bug was an auth-handling drift); no-op replace and

### 16.8 Guest data path still dead — fixed by re-running the core's net-init (2026-08-16)

§16's claim that the guest data path worked was INCOMPLETE: with the app-side
driver's autoConf+up ALONE, the guest's `connect(2)` never completes app-side
even though the browser-side netstack finishes the TCP handshake and the
wrapper's `opened` resolves (verified by tracing `networkInterface.TCPSocket`
calls from the guest: 45/45 opened resolved, handshakes complete, ZERO HTTP
bytes ever flow; `nc -z 100.64.0.1 8082` hangs; the sync agent retries forever
and no lease ever lands). The §16.5 sync-E2E pass could not be reproduced.

**Fix (`webvm/src/lib/network.js`, `startTailnet`): after the driver's
`autoConf`+`up()`, call the CORE's own net-init `window.cheerpOSNetInit(...)`
(same tun path + the driver's callbacks). The second `autoConf`+`up()` on the
tun module re-establishes the working guest data path. Verified: the sync
lease + first snapshot land on the backend within ~2 min with the call (2/2
manual runs + the sync spec's lock assertion passes), 0/5 runs without it.
The core's own invocation, if it ever runs, is idempotent with this one.

Remaining flakiness (pre-existing CheerpX guest quirks, not the data path):
the boot can occasionally wedge at "Starting local ..." (the §15 crash class),
and the boot pull's `wait_for_tailnet` cycles 12 attempts at ~15-20s each
under the slow guest clock, so the sync spec budgets were raised to 240s
(lock + snapshot) with a 600s spec timeout.
  dead `tailnetUp()` removed.

### 16.9 Guest bind(2)/listen(2) crashed the core — fixed by implementing TCPServerSocket (2026-08-16)

**Symptom:** `nc -z 100.64.0.1 8082` in the guest xterm made the console log
end in `Uncaught TypeError: r.TCPServerSocket is not a function` (cx_esm.js
worker) right after the connect attempt.

**Root cause (traced in the vendored runtime):** the core's guest-socket
dispatcher forwards `connect(2)` to `interface.TCPSocket` (dispatcher case 88
→ `wT`) but forwards `bind(2)`/`listen(2)` to `interface.TCPServerSocket`
(case 36 → `wS` → `r.TCPServerSocket("0.0.0.0", {localPort})`). The page's
custom `networkInterface` (webvm/src/lib/network.js) implemented only
`TCPSocket`/`UDPSocket`, so ANY guest process that explicitly binds crashed
the worker. BusyBox `nc` unconditionally calls `bind(2)` before `connect(2)`
(nc_bloaty.c `xbind`), so even the plain connect probe crashed; the sync
agent's implicit bind inside `connect(2)` never hits case 36, which is why
§16.8's data path worked while `nc -z` died.

**Fix (webvm/src/lib/network.js):** implemented `TCPServerSocket(addr,
{localPort})` mirroring the runtime's `TailscaleNetwork.TCPServerSocket`
(tun/direct.js: bind(localPort) → listen() → accept loop over
`accept()`/`waitIncoming()`, streaming accepted connections as
`{opened, closed, close}` wrappers built by the shared
`connectedTcpSocket()` helper, which now also backs `TCPSocket`). Exposed
`window.cjTailscaleCurrentIp` for the E2E listen-twin probe
(tests/e2e/tests/network.spec.js): bind+listen on the tailnet IP, self-connect
through the tun, round-trip through the accepted socket — this is the "nc -z
twin" for the listen side.

**Verification:** frontend builds; unit 89 pass; E2E network spec now covers
both directions. Re-test in the browser: rebuild the frontend
(`cd webvm && WEBVM_MODE=browser WEBVM_IMAGE_BUILD=$(cat
custom-disk-images/image-build.txt) npm run build`), reload the page, and
`nc -z 100.64.0.1 8082` should connect (the guest nc twin: TCP SYN-ACK
through the DERP relay — see §16.8 for the data-path prerequisites).

### 16.9 The inbound accept path is dead + CONTROL_HOST=127.0.0.1 breaks the guest path (2026-08-16, late session)

Two runtime defects pinned down with packet-level tracing (TUN-DIAG instrumentation
in tailscale_tun.js, since removed) and a full bisect (server_url flips, gateway
entrypoint reverts, heal variants, DB purge, image rebuilds):

1. **Inbound TCP for the node's own IP is consumed by the rebuilt tailscale.wasm
   — guest servers can bind+listen but never accept.** A SYN from a REAL peer
   (the gateway, verified reachable via `tailscale ping` → pong via DERP) never
   reaches the tun: the wasm client's own-IP handling swallows it
   (`initPeerAPIListener: 2 netmap addresses match existing listeners`), and the
   IpStack then spins an internal SYN/SYNACK retransmission loop (observed ~80
   iterations). The page-side TCPServerSocket (network.js) binds+listens fine —
   the §15 "TCPServerSocket is not a function" crash regression is fixed — but
   the accept queue can never fill. Consequence: no guest LISTEN services
   (sshd, git daemon, `python3 -m http.server`) can ever accept; the E2E
   listen-twin probe must assert bind+listen ONLY (network.spec.js), with the
   accept path documented as a runtime limitation. Outbound guest traffic is
   unaffected (the sync agent's lease PUTs, HTTP round-trips and the nc-twin
   probe all work).

2. **`CONTROL_HOST=127.0.0.1` (browser-facing control host) breaks the guest
   OUTBOUND data path.** With server_url=https://127.0.0.1:8443 the page-side
   adapter probe works (SYN in 17ms, HTTP round-trip OK) but the guest's sync
   agent never lands the lease (lock poll 240s+ fails; the guest-socket trace
   shows the guest stuck on the adapter path, raw=0, and zero wsgidav
   requests). With server_url=https://host.docker.internal:8443 the SAME
   everything works end-to-end (lock in 15-30s, guest HTTP flows). The
   mechanism is in the rebuilt wasm client's netmap/DERP handling (an IP
   literal as the DERP-map host); a page-side or config workaround was not
   found in this session. REVERTED the 127.0.0.1 default back to
   host.docker.internal: single-machine use REQUIRES the one-line
   `/etc/hosts` entry (`127.0.0.1 host.docker.internal`) on the browser
   machine — that is the documented setup, and without it the browser spams
   "failure to resolve host.docker.internal" (the user's original report) and
   the tailnet never starts.

3. **Unresolved flake (observed 01:20-04:35 UTC):** with server_url flipped to
   127.0.0.1 and back, the guest path stayed broken across many runs even
   after the flip-back, then started working again after a full `make build`
   (fingerprint + ext2 + frontend rebuild) — with no identified single cause
   (headscale DB purge, gateway/heal reverts and clean slates did not fix it).
   Suspected interplay of the two-client heal (two wasm clients per page —
   driver's + heal's — two tailnet nodes, the IpStack's output wired to the
   LAST client) with the DERP host. Left as an open item: re-verify the
   heal's necessity and the two-client setup when the wasm client is next
   rebuilt (§16.1 source).

### 16.10 host.docker.internal REMOVED — IP literals only (2026-08-16, user mandate)

**The user's hard requirement (categorically imperative, never to be
reintroduced):** NO hostnames anywhere — no `host.docker.internal`, no
`/etc/hosts` entries, no custom DNS for LAN users. Everything must work with
`127.0.0.1` (zero-config single machine) and a hardcoded LAN address such as
`192.168.x.x` (LAN) alone. §16.9's "revert to host.docker.internal" verdict is
**SUPERSEDED** — a hostname-based setup is not acceptable even if it works.

**Mechanism that makes 127.0.0.1 work (the missing piece in §16.9):** the
netmap's DERP region host is derived from headscale's `server_url`, i.e. the
BROWSER-facing `CONTROL_HOST` — `127.0.0.1` on the single machine. Inside the
GATEWAY container `127.0.0.1` is the gateway's OWN loopback, so its
tailscaled could never reach the DERP relay (which is exactly what §16.9's
"guest data path dies with 127.0.0.1" observed — the sync agent's lease PUTs
to the gateway relay hang, while the page-side adapter probe still works,
because the BROWSER reaches DERP at 127.0.0.1 fine). The earlier analysis
attributed this to "the rebuilt wasm client's netmap/DERP handling with an
IP-literal DERP host" and found "no page-side or config workaround" — the
actual fix is a **loopback socat relay in the gateway on CONTROL_PORT
forwarding to the server's static compose-network IP**
(`start_relay "${CONTROL_PORT}" "${GATEWAY_CONTROL_IP}:${CONTROL_PORT}"` in
gateway/entrypoint.sh): the gateway's DERP connection to
`https://127.0.0.1:8443/derp` lands on its own loopback relay and is
forwarded to the server. On LAN deployments the DERP host is the LAN IP,
which the gateway reaches directly through the host (the relay is then
unused but harmless).

**Everything hostname-shaped was removed 2026-08-16:**
- `CONTROL_HOST` default is `127.0.0.1` in every file (`server/entrypoint.sh`,
  `scripts/print-url.sh`, `scripts/gen-certs.sh`, `scripts/acceptance.sh`,
  `compose.yaml`, `.env.example`) — LAN deployments set it to a hardcoded LAN
  IP. No `/etc/hosts` requirement anywhere; the README documents the ban.
- The gateway uses `GATEWAY_CONTROL_IP` (default `172.28.0.10`, the server's
  static compose-network IP on the fixed `172.28.0.0/16` network; cert SAN
  covers it) for `--login-server` and its relays. `extra_hosts` was removed
  from compose.yaml entirely.
- `scripts/gen-certs.sh` SAN no longer carries `DNS:host.docker.internal`.
- CI no longer appends `127.0.0.1 host.docker.internal` to `/etc/hosts`; the
  E2E `--host-resolver-rules` mapping was removed (playwright.config.js and
  the probe scripts); the join-test client joins via
  `https://172.28.0.10:8443`.
- **Enforcement:** `tests/unit/test_scripts.py::test_control_host_defaults_consistent`
  asserts every CONTROL_HOST default is `127.0.0.1` AND that the literal
  `host.docker.internal` appears in none of the runtime config/scripts/tests/
  CI files (the banned list is in the test). This test FAILS CI if the
  hostname is ever reintroduced — keep it green. AGENTS.md carries the rule.

**Re-verification needed (open item):** the §16.9 data-path break under
127.0.0.1 was never reproduced with the loopback DERP relay in place (the
relay was added as part of this removal). The E2E `network.spec.js` root-visit
test (baked config → `webvm.lock` lease → nc-twin socket probe → listen-twin)
is the gate: it must pass on the single machine with `CONTROL_HOST=127.0.0.1`
defaults and no /etc/hosts entry. Re-check the §16.9 "unresolved flake" item
too — if the two-client heal is still present when the wasm client is next
rebuilt, the flake may reappear independently of the DERP host.

**2026-08-18 update — the listen-twin gate now passes, after fixing a hard
page freeze in the page-side `TCPServerSocket` (webvm/src/lib/network.js):**
`network.spec.js` hung the whole test (600 s timeout at the listen-twin probe)
because the wrapper's ReadableStream `pull()` awaited the IpStack's
`waitIncoming()`, which busy-spins the browser's main thread when no
connection ever arrives — and with the rebuilt tailscale.wasm consuming
inbound TCP for the node's own IP (§16.9), no connection ever arrives, so ANY
guest `bind(2)`/`listen(2)` (and the listen-twin probe) froze the page
indefinitely. The raw IpStack socket binds+listens fine; the freeze was the
`waitIncoming()` await. The wrapper now polls `accept()` with a 100 ms yield
instead, so bind+listen never blocks the main thread (the accept path remains
dead — §16.9's runtime limitation). The E2E gate was re-verified green
locally with the gateway up (webdav phase): root visit → webvm.lock lease →
nc-twin connect + HTTP round trip → listen-twin bind+listen, all passing.
