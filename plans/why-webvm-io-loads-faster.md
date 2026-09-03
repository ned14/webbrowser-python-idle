# Why webvm.io boots faster than webvm.nedprod.com (2026-09-02)

Question: `https://webvm.io/alpine.html` (upstream WebVM) reaches a usable
desktop faster than `https://webvm.nedprod.com/alpine.html` (this repo's
custom deployment). Both are Cloudflare-fronted. Findings below.

## CORRECTION (2026-09-02, later same day): direct-to-origin is NOT slower

An earlier version of this file claimed removing Cloudflare makes range
reads ~5× slower (one-shot curl: 331 ms direct vs 68 ms via CF). That was a
MEASUREMENT ARTIFACT: one-shot curl opens a fresh TCP+TLS connection per
read (~4 RTTs from a US vantage to the UK origin ≈ 330 ms), but the VM's
XHR byte device REUSES keep-alive connections. Re-measured the way the VM
actually reads (one reused connection, sequential 128 KiB range reads, 15
warm reads each, Node https keepAlive agent, direct via custom lookup to
82.47.22.78 with Host/SNI preserved):

    via Cloudflare:  median 71-77 ms   (15/15 reads reused the socket)
    direct to origin: median 55-57 ms  (15/15 reads reused the socket)

Direct is ~15-20 ms (~20 %) FASTER per read than via CF from this US
vantage. The origin box was also mislabeled "home broadband" — that was an
assumption, unverified (the repo itself calls it "a tight-disk VPS"; IP is
in a BT-looking range; actual access type unknown). Caveats: this is one
vantage + one moment; a UK user may see an even larger direct advantage
(shorter public path), a far-away/lossy user a smaller one. The real
conclusion stands on the mechanism, not the label: CF's per-read cost is
dominated by the same origin leg the direct path takes, plus CF's own hop;
once connection setup is amortized, direct is competitive-or-better.

### Implication for the proposed split-brain setup
Serving HTML/wasm/JS via CF while the ext2 range reads go DIRECT to the
origin (a proxied-off subdomain with a CORS allow-header on nginx) is
viable and would likely IMPROVE boot time for most visitors (per-read
55-70 ms direct vs 71-77 ms via CF, and no CF 206-is-DYNAMIC constraint).
Risks to weigh: per-visitor geography variance, and the origin's upload
capacity if many cold boots happen at once (only ~86 MB per cold boot is
actually read; repeat visitors hit the browser cache). R2/edge serving
remains the best option for scaling; direct-to-origin is the cheapest
experiment that can beat today's ~43 s boot.

## SPLIT-BRAIN SHIPPED + MEASURED (2026-09-02, later)

Implemented and deployed the configurable facility + the split setup:

- **Facility**: `WEBVM_DISK_BASE_URL` (single home: scripts/lib/
  webvm-common.sh; default empty = same-origin, byte-unchanged). Flows to
  (a) the frontend bake (`config_public_alpine.js` + vite define), (b) the
  CSP connect-src (`render-webvm-config.py --disk-origin`, entrypoint), (c)
  the nginx CORS/preflight answer on the image location (allow-origin *,
  expose Content-Range/ETag, OPTIONS 204 with allow-headers Range), and (d)
  the server-cert SAN (`gen-certs.sh` adds the disk hostname). Unit tests
  pin each piece (280 python + 80 vitest green).
- **Deployment**: `disk.webvm.nedprod.com` A → 82.47.22.78 (proxied OFF —
  direct to origin, no CF in the read path); box .env now has
  WEBVM_DISK_BASE_URL=https://disk.webvm.nedprod.com; cert regenerated with
  DNS:disk.webvm.nedprod.com and verified served over SNI.
- **Verified live**: the baked page URL is absolute to the disk host; CSP
  carries https://disk.webvm.nedprod.com; OPTIONS preflight → 204 with
  allow-headers Range + expose Content-Range/ETag; range GET → 206 + ACAO.
  During a full live boot ALL 676 ext2 reads went to disk.webvm.nedprod.com
  (0 non-206/failures).
- **Boot timing (Playwright, same Mac, ~same hour as the "before")**:
  - nedprod BEFORE (reads via CF): sized ~30-31 s, content ~42-43 s.
  - nedprod AFTER (reads direct): sized ~26 s, content **~36-38 s** (~14 %
    faster, matches the ~20 % per-read latency drop × 660 reads ≈ 6 s).
  - webvm.io same window: ~22-24 s (first run 40 s — their edge warm
    variance). Upstream still faster: WS disk server + leaner pipeline +
    a 1.5 GB image served by purpose-built infra; closing the rest of the
    gap would need their-style WS disk serving or R2.
- **CAVEAT (public readers)**: disk.webvm.nedprod.com terminates TLS at the
  origin with the PRIVATE-CA cert — only browsers that trust that CA (the
  owner's, plus E2E with ignoreHTTPSErrors) accept it. Public visitors
  would hit a cert error unless (a) the private CA is distributed, (b) the
  disk host gets a public cert (Let's Encrypt on the origin), or (c) the
  disk host is CF-proxied (public cert, but reads then go CF→origin again —
  still faster than today IF CF edge-caches the image; CF does not cache
  206s, so proxying alone does not regain the direct-read win).


## Measured (headless Chromium, same Mac, sequential runs)

| Metric | webvm.nedprod.com | webvm.io (upstream) |
|---|---|---|
| Disk image | 193 MB (202375168 B) | **1.5 GB** (1572864000 B) |
| Disk served from | our nginx on a ~1 GB-RAM UK VPS via CF | Leaning's `disks.webvm.io` infra via CF |
| Disk read transport | per-range HTTPS GETs (`HttpBytesDevice`, `bytes=`), CF `cf-cache-status: DYNAMIC` (206s never edge-cached) | **WebSocket** `wss://disks.webvm.io/alpine_20251007.ext2` via `CloudDevice` |
| Canvas sized | ~30-31 s | ~14 s |
| First screen content (>0.2 % non-black) | ~42-43 s | ~22-24 s |
| Per-range read latency, KEEP-ALIVE (see CORRECTION above) | via CF median ~71-77 ms; DIRECT to origin median ~55-57 ms | n/a (WS-only) |

The upstream page boots to visible content in roughly **half** the time
despite an image **8× larger**.

## Root cause: the boot is DISK-READ-LATENCY-bound, and the read paths differ fundamentally

Earlier instrumentation of this repo's boot (~660 block reads of 128 KiB
before the desktop, all byte-verified correct) plus today's numbers show
the boot time ≈ reads × per-read round-trip latency at near-serial
concurrency (~1.5×): 660 reads × ~65-100 ms ≈ 43 s. Bandwidth is NOT the
limiter (only ~86 MB actually read; even 2 MB/s effective would be ample).

Why each read is so expensive on nedprod:
1. The browser's range GET hits Cloudflare's edge... and CF does NOT serve
   it: 206 partial content is `DYNAMIC` (not edge-cached), so every block
   read is proxied CF → origin VPS (UK) → CF → browser. Measured median
   ~68 ms with a heavy tail (max 473 ms — origin is a small, busy VPS).
2. The origin is a ~1 GB-RAM, 3 GB-free-disk VPS that also serves the whole
   site and rebuilds itself; it is not a disk-optimized endpoint.
3. CheerpX's HTTP byte device issues reads that are effectively serialized
   (~1.5× concurrency), so per-read latency translates ~1:1 into boot time.

Why upstream is fast despite 1.5 GB:
- `CloudDevice` talks to a dedicated WebSocket disk server
  (`disks.webvm.io` — Leaning's own, CF-fronted, built for this): one
  persistent connection, pipelined/streamed block requests, no per-read
  HTTP/TLS/CF-overhead, presumably far lower per-read latency and higher
  parallelism, and their origin/edge can stream the 1.5 GB image at speed.
- Their canvas sizes at ~14 s (device/engine init + first reads) vs our
  ~30 s — the whole read pipeline is leaner.

Minor contributors (not root cause):
- Page TTFB through CF: nedprod ~100 ms vs webvm.io ~49 ms (both fine).
- nedprod HTML is `DYNAMIC`; webvm.io HTML is edge-cached (`HIT`) — only a
  few ms on page load, not the boot time.
- nedprod's console shows one CSP error (Cloudflare beacon) — cosmetic.
- Different image contents/sizes and upstream CheerpX pin; the transport
  difference dominates.

## Options to close the gap

A. **Move the ext2 to Cloudflare R2** (same CF account family as the
   deployment; R2 supports HTTP range reads, is served from the CF edge,
   and can be fronted by a cache rule). No app code change — just point
   `diskImageUrl` at R2 (keep `?v=<fingerprint>` for immutability).
   Expected: per-read latency drops from ~65-95 ms to ~10-25 ms (edge
   served, big bandwidth), cold boot ~43 s → ~15-25 s; repeat visits are
   already fast via the browser cache. Cost: pennies; needs an R2 bucket +
   API credentials (user's Cloudflare account).
B. **Implement a CloudDevice-style WebSocket disk server** on the VPS
   (mimic upstream) and switch the app to `CloudDevice` — removes per-read
   HTTP/CF overhead and allows pipelining, but reads still traverse
   CF → UK VPS per round trip, so gains are smaller than A and the change
   is much bigger (server + protocol + app wiring).
C. **Tune the current HTTP path** — limited: read granularity/parallelism
   live inside the vendored CheerpX runtime; nginx/CF buffering tweaks
   won't remove the per-read origin round trip.
D. Accept it (document that 2× slower boot is the price of the current
   architecture).

Recommended: **A (R2)** — largest win, smallest change. B is the fallback
if R2 isn't acceptable. Removing Cloudflare entirely is NOT recommended:
measured ~5× slower per read direct-to-origin (see above), plus loss of
CF's TLS/DDoS/static-caching benefits. Note R2 still fronted by CF means
the user-facing leg is unchanged and the ORIGIN leg disappears entirely —
the actual fix for the ~68 ms floor + 473 ms tail.

## Harnesses added
- tests/e2e/boot-timing-compare.mjs — sequential canvas-size / first-content
  timing that works for light AND dark themed WebVM pages.
- tests/e2e/range-latency-probe.mjs — per-range read latency/throughput
  probe for bytes-mode disk URLs.
- tests/e2e/page-probe.mjs, boot-visual-compare.mjs — page-state probes
  (visual-compare was abandoned: screenshots unusable in this session's
  tooling).

## Read SIZE vs concurrency — the 4 KiB question (2026-09-03)

Question: the cold boot issues ~660 *sequential* 128 KiB range GETs
(84-86 MB of the image); would smaller (4 KiB) or more concurrent reads be
faster? Measured on this Mac against the LIVE endpoints, keeping the boot's
read shape (fixed offset, reused keep-alive connections, median of 6+2-warm
samples per cell), request latency split into time-to-headers ("hdr", the
fixed per-request cost) vs body transfer ("body"):

| size | conc | direct (disk.webvm.nedprod.com) | via Cloudflare |
|---|---|---|---|
| 4 KiB   | 1 | median 53.3 ms (hdr 53.1 + body 0.2) | 60.7 ms (60.2 + 0.5) |
| 64 KiB  | 1 | 56.1 ms (55.7 + 0.4)                | 62.9 ms (61.5 + 1.4) |
| 128 KiB | 1 | 56.3 ms (54.3 + 2.0)  **= today**   | 64.1 ms (60.6 + 3.4) |
| 512 KiB | 1 | 61.1 ms (54.0 + 7.1)                | 67.7 ms (60.0 + 7.7) |
| 1 MiB   | 1 | 71.4 ms (54.4 + 17.0)               | 75.4 ms (59.9 + 15.5) |
| 128 KiB | 4 | 60.3 ms — eff 11.0 MB/s             | 69.0 ms — eff 8.8 MB/s |
| 128 KiB | 8 | 65.8 ms — eff 19.3 MB/s             | 73.7 ms — eff 10.3 MB/s |

Findings:

1. **Per-request cost is ~95 % FIXED latency, not transfer.** Time-to-headers
   is ~54 ms (direct) / ~60 ms (CF) at EVERY size from 4 KiB up; the 128 KiB
   body adds only ~2-3 ms, a 1 MiB body only ~15-17 ms. The fixed cost is
   network RTT + origin/CF processing per request. This is why the boot
   model works: 660 × ~56 ms ≈ 37 s direct ≈ the measured 36-38 s content
   time; 660 × ~64 ms ≈ 42 s via CF ≈ the measured 42-43 s.
2. **4 KiB reads would be ~32× SLOWER, not faster.** 4 KiB costs the same
   ~53-60 ms fixed latency as 128 KiB (its body is 0.2 ms — nothing is
   saved), but the boot read-set grows from ~660 requests to ~22,000:
   ~20 minutes serial vs ~37 s today. The only way smaller reads could win
   is ~32× the concurrency, which neither the runtime nor the browser's
   per-origin pools can provide.
3. **Larger reads are the lever, not more of them.** If the runtime read in
   256 KiB/512 KiB/1 MiB units the network portion would fall to ~20 s /
   ~10 s / ~6 s at today's latencies. But the read unit is the CheerpX
   overlay record = 128 KiB (32 × the image's 4 KiB ext2 blocks — fixed in
   the runtime core, verified in plans/diagnose-flaky-boots.md; the public
   device API is only `create(url)`). We cannot change it without forking
   the runtime.
4. **Concurrency cannot be raised from the app.** The runtime issues reads
   near-serially (~1.5× in flight — dependency-bound: each 128 KiB record is
   demanded by the guest before the next). No public API exposes read depth
   or pipelining (index.d.ts: BlockDevice has no knobs). The network/server
   side is NOT the limit: HTTP/2 + open_file_cache are on, and the server
   sustains 19 MB/s at 8-deep 128 KiB reads — it is idle because the client
   sends ~1 request at a time.

### What this means for the options list

- **Option C (tune the HTTP path) is now closed with data**: read size and
  depth are runtime constants; server tuning cannot help a client that
  issues one request at a time.
- The win available WITHOUT touching the runtime is to make those fixed-cost
  round trips disappear: serve the guest's range reads from the browser HTTP
  cache instead of the network. That is exactly what the leading-32 MiB
  warm (startEarlyBootFetch) already does for the leading 32 MiB (~37 % of
  the ~86 MiB boot read-set). Extending it to the whole read-set (or the full
  193 MiB image) on the split-brain disk host (WEBVM_DISK_BASE_URL) is the
  highest-value experiment: it replaces ~660 × 56 ms serial round trips with
  one (or a few) bandwidth-bound streams at ~17-40 MB/s, i.e. a network
  portion of a few seconds. The 2026-08-30 reason for capping at 32 MiB —
  the warm competed with the runtime download over the SAME connection —
  needs re-testing now that the disk host is a separate origin from the
  page/wasm. (Repeat boots already get this via maybeWarmFullImage post-desktop.)
- Per-request latency reductions (Option A: R2/edge) still help in
  proportion: 660 × ~15 ms ≈ 10 s network portion, app unchanged.

## FULL-IMAGE CF CACHING: single-shot warm + edge-served ranges (2026-09-03)

Hypothesis tested: instead of 660 sequential 128 KiB range GETs through CF
(which the doc above records as `cf-cache-status: DYNAMIC` — never
edge-cached), pull the whole ext2 once as a plain 200 so Cloudflare caches
the full object, then let the guest's range reads be served from the edge.

Result: **the mechanism works, but the ext2 is not cacheable at CF today —
and a per-PoP warm is needed.** Verified live against webvm.nedprod.com:

1. **CF serves byte-range requests from a cached full 200.** A cached
   immutable asset answered `Range: bytes=…` with a 206 AND
   `cf-cache-status: HIT` (`age` advancing), including the `content-range`
   + `etag` headers the CheerpX device needs. 206s are not *stored* by CF
   (the old `DYNAMIC` observation stands for never-cached objects), but a
   cached full object IS served in slices.
2. **The ext2 full 200 is NOT cached by default.** `curl` of the whole
   image (no Range, 202375168 B, `cache-control: public, max-age=31536000,
   immutable`) returns `cf-cache-status: DYNAMIC` — `.ext2` is not in CF's
   default-cacheable extension set, so CF bypasses it regardless of the
   origin headers. A **Cache Rule ("Cache Everything")** on
   `/custom-disk-images/*` is required (the same requirement Option A's R2
   plan already assumed). Same for the disk host if it is CF-proxied.
3. **Per-read latency from a warm edge is ~2-3× lower and tail-free.**
   Same Mac, keep-alive, sequential 128 KiB-ish ranges:
   - ext2 via CF, DYNAMIC (origin leg): median **153.6 ms** this run
     (75-352 ms — the origin leg is variable; earlier runs 60-70 ms).
   - cached font via CF, HIT (edge-served): median **24.9 ms** (23-27 ms,
     no tail) — *faster than direct-to-origin (~55 ms) from this vantage*,
     because the CF edge is nearer than the UK origin VPS.
   Even at a conservative edge median of ~25 ms, 660 sequential reads ≈
   ~16 s of network time vs ~42 s via the origin leg — and the origin's
   ~86 MB/boot upstream load disappears entirely.
4. **Cold caches are per-PoP and per-fingerprint.** CF stores the object
   per edge data-centre: after enabling the rule, the first visitor to each
   PoP still reads through to the origin (that full 200 must traverse the
   PoP once before ranges go HIT), and each image rebuild changes the `?v=`
   URL, invalidating every PoP. Pre-warm = curl the full fingerprinted URL
   from each region you care about (or accept lazy warming). The box's own
   UK PoP warms from the 6-hourly reset cron trivially.
5. **It does not change the read pattern.** The guest still issues ~660
   sequential 128 KiB range GETs; the win is only cheaper per-read latency.
   (The stronger effect — no per-read latency at all — is the same-browser
   full warm into the HTTP cache, which `maybeWarmFullImage` already does
   post-desktop for repeat boots.)

Implication vs the split-brain decision (2026-09-02): the split-brain
moved reads OFF Cloudflare to cut ~15-20 ms/read (direct ~55 ms vs CF
DYNAMIC ~71-77 ms). A cache-ruled + warmed CF path (~25 ms edge-served)
would now beat BOTH and unload the origin. Two options:

- **O1 (no R2): Cache Rule on webvm.nedprod.com `/custom-disk-images/*`**
  (Cache Everything, respect origin `max-age`/immutable; keep `?v=` in the
  cache key so rebuilds bust it), keep WEBVM_DISK_BASE_URL empty
  (same-origin reads), and warm each PoP after each build. Zero code
  change, dashboard-only; ~16 s network portion from warm PoPs, origin load
  gone for repeat visitors per PoP. Per-PoP/per-fingerprint warm-up is the
  operational cost.
- **O2 (Option A, R2):** same outcome with one global persistent cache
  (no per-PoP warm dance, survives rebuilds until re-warmed once), at the
  cost of setting up R2 + moving the image there.

Recommended experiment order: enable the Cache Rule on the live zone, warm
the current fingerprinted URL from the box, and re-run the boot-timing
harness — expect content time to move from ~36-43 s toward ~25 s.

### VERIFIED ON LIVE (2026-09-03, later) — Cache Rule now in place

User added the CF Cache Rule; re-tested the CF-fronted URL:

- Full 200 (no Range): `cf-cache-status` DYNAMIC → **MISS** → **HIT**
  (was never cacheable before the rule).
- 128 KiB range reads on the cached object: **206 + `cf-cache-status: HIT`**
  with `content-range`/`etag` present. Sequential 128 KiB reads from a warm
  edge measured **median 22.0 ms** (20.6-86.1 ms) → ~660 reads ≈ **~14.5 s**
  network portion vs ~42 s DYNAMIC / ~36-38 s direct-to-origin.
- Query string is part of the cache key: the exact fingerprinted URL
  (`...ext2?v=cb8527ddb797`) warms and serves ranges independently of the
  bare URL.

**Routing caveat (blocks the live win):** the deployed page is baked with
`WEBVM_DISK_BASE_URL=https://disk.webvm.nedprod.com` (see the baked
`_n`/`vn` in the served bundle), and `disk.webvm.nedprod.com` is STILL
proxied OFF (DNS → 82.47.22.78, `server: nginx/1.30.4`, no CF headers) —
live boots therefore read direct-to-origin and never touch the CF cache.
To realize the measured win the reads must go through the CF-fronted host:
clear `WEBVM_DISK_BASE_URL` in the box `.env` (same-origin reads via
webvm.nedprod.com, which ALSO fixes the private-CA cert problem for public
visitors — CF presents its own public cert), or proxy the disk host
(orange cloud) + same Cache Rule. Either way, warm the fingerprinted URL
once per PoP after each image rebuild.

### A/B ON THE LIVE BOX (2026-09-03) — WEBVM_DISK_BASE_URL cleared

Applied the routing fix on webvm.nedprod.com: removed
`WEBVM_DISK_BASE_URL` from the box `.env`, rebuilt the frontend (baked
`_n=""` → same-origin `/custom-disk-images/webvm-custom-disk.ext2?v=
cb8527ddb797`), rebuilt the server image, `WEBVM_TAILNET=off docker compose
up -d` (stack healthy, image fingerprint unchanged so the CF cache stayed
warm). Boot timing (`tests/e2e/boot-timing-compare.mjs`, fresh headless
Chromium per run, DUB edge):

| config | sized | firstNzb / content |
|---|---|---|
| BEFORE (disk.webvm.nedprod.com direct) | 25-27 s | 37 s |
| AFTER (same-origin reads, CF edge HIT) | 13-16 s | **17-18 s** (one cold run 24 s) |

~2× faster content time (37 s → ~17-18 s), matching the per-read model
(~22 ms × 660 reads ≈ 14.5 s network portion vs the old origin-leg reads).

**OPERATIONAL CAVEATS discovered on the live change:**
- The new Cache Rule also caches the site's HTML: `alpine.html` came back
  `cf-cache-status: HIT` (age growing) with the OLD baked `_n`, so public
  visitors kept the pre-change page until the entry is purged. The A/B ran
  on a cache-busting query (`alpine.html?x=cf`) which keyed a fresh MISS →
  origin. Recommend purging `/alpine.html` (or excluding HTML from the
  rule) after this change.
- After any guest-image rebuild the `?v=` fingerprint changes and every PoP
  must be re-warmed with one full-200 fetch of the new URL.
- `.env` backup on the box: `.env.bak-cf-experiment`.

### SINGLE-SHOT CLIENT PREWARM — MEASURED NEGATIVE on Chromium (2026-09-03)

Hypothesis: fetch the WHOLE ext2 into the browser HTTP cache in one shot
before/during the boot, so the guest's 660 × 128 KiB range reads hit the
local cache instead of the network. Measured three ways (fresh headless
Chromium per run, live page, same-origin CF-cached ext2):

1. **Zero-latency floor** (every range served instantly from a local copy —
   the best case a completed prewarm could achieve): **sized 7 s, content
   11 s** (vs 17-18 s CF-cached / 37 s direct). So the *potential* prize of
   eliminating per-read cost is ~6-7 s.
2. **Prime = whole image as ONE full 200** (193 MB in 3.9 s from the CF
   edge), then boot: the boot STILL issued **675 range requests to the
   network** and reached content in **16 s** — unchanged. The 200 was NOT
   retained by the Chromium HTTP cache (`fetch(..., cache:"only-if-cached")`
   → network error, with a persistent profile and a 1.5 GB disk cache; a
   1.9 MB control resource IS retained, so this is not a harness artifact).
3. **Prime = whole image as ONE full-file 206** (`Range: bytes=0-…`, 3.3 s):
   same result — **672-673 network range requests** during the boot, content
   **18-21 s**. Chromium does not serve the guest's XHR byte-range reads
   from a stored full response (partial-content cache rules / fetch-mode
   semantics; the 206 fragments the app's current 32 MiB warm relies on are
   likewise not reusable for later range requests).

Conclusion: **client-side single-shot prewarm does not reduce the first
boot on Chromium** — the guest's range reads cannot be served from the
browser cache and always traverse the network. The measured 17-18 s IS the
practical cold-visitor floor with the CF-edge cache (per-read ~22 ms × 660
reads overlaps engine/guest work); the ~11 s zero-latency floor is only
reachable by making reads local to the GUEST, which the IndexedDB overlay
already provides for repeat boots. This also implies the app's
`startEarlyBootFetch` 32 MiB warm and post-desktop `maybeWarmFullImage`
(no-Range 200) do not work via the browser HTTP cache as their comments
assume — repeat-boot speedup comes from the IDB overlay, not those fetches.

## REMOVED (2026-09-03): the client-side prewarm fetches

Both prewarm fetches deleted from `webvm/src/lib/WebVM.svelte` (commit-free
local change; builds + 80/80 frontend tests green):

- `startEarlyBootFetch`'s leading-32 MiB range fetch (`Range:
  bytes=0-33554432`, `priority:"low"`) — 206 responses are not cached by
  Chromium, so it never produced cache hits for the guest's reads. The
  function keeps its REAL value: starting the CheerpX runtime import + both
  device creations at mount, in parallel with terminal setup.
- `maybeWarmFullImage()` + `fullWarmDone` + both call sites (watchdog
  post-desktop, and the post-`cx.run()` loop) — the whole-image no-Range
  200 is not retained by Chromium's cache (a 193 MB response exceeds its
  per-entry storage; `fetch(cache:"only-if-cached")` fails while a 1.9 MB
  control is retained) and even if retained would not serve the guest's
  XHR range reads.

Verification evidence (live site, fresh Chromium per run): an exact-range
128 KiB request repeated twice hit the network BOTH times (no 206 caching
even for byte-identical re-requests); after a completed whole-image 200 or
full-file 206 prime (3.3-3.9 s from the CF edge), the boot still issued
672-675 range requests and reached content in 16-21 s — no change.

**Operational caveat (CF edge warmth):** `maybeWarmFullImage` was also the
only automatic source of full-200 fetches, i.e. the thing that keeps each
Cloudflare PoP's full-object cache warm (range reads never populate it).
With it gone, a PoP is only warm for range reads after an external full-200
warm of the current `?v=` URL — e.g. the box cron after each image rebuild
(the origin box's curl only warms the PoP(s) its egress reaches). If remote
PoPs matter, replace with a scheduled warm (curl the full fingerprinted URL
per region) rather than re-adding a per-visitor 193 MB download.

## REINSTATED as HEAD-gated edge warm + ETA recalibration (2026-09-03, later)

The caveat above was actioned: `maybeWarmFullImage` is back in a form that
keeps CF warm WITHOUT a 193 MB download per visitor.

- **`maybeWarmCfEdge()`** replaces it (WebVM.svelte): post-desktop, issue a
  cheap `HEAD` (no body) to the image URL and read `cf-cache-status`.
  Verified live: CF answers `HIT` (+age) for a cached `?v=` object and
  `MISS` for an uncached one, so a HEAD tells us the PoP state in one
  round trip. `HIT`/`REVALIDATED` (or no header = not behind CF) → nothing
  is fetched; `MISS`/`EXPIRED` → one low-priority full-200 GET populates
  that PoP. Cost: one HEAD per session, and one ~193 MB download per
  (PoP, image version) — the minimum that keeps range reads edge-served.
  Range reads still never populate the full-object cache, so without this
  fetcher CF would evict/expire the 200 and reads would silently drop to
  the origin leg again.
- **Boot-ETA recalibration** for the CF-edge regime. Measured live
  (pill-appear → 'webvm desktop ready'): 23.5-25.6 s boot at a 23-26 ms
  steady per-read latency (UK, DUB edge). New model:
  `BOOT_ETA_SECONDS=30`, `LATENCY_REF_MS=24`, slope `0.6` s/ms unchanged,
  floor `22` s (was 75/73/60 for the DYNAMIC-leg era). The pill no longer
  starts at ~60+ s for a ~25 s boot.

Both changes are in commit 1afe9ac and deployed to webvm.nedprod.com
(verified live: pill starts at 00:30, boot ~24-25 s to the desktop marker).
