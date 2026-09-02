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
