# Let's Encrypt facility for the web server (2026-09-03)

## Why

`webvm.nedprod.com` is served via Cloudflare (browsers see CF's edge cert), but
the split-brain disk setup (plans/why-webvm-io-loads-faster.md) reads the ext2
DIRECT from `disk.webvm.nedprod.com` — a Cloudflare-proxied-OFF DNS record at
the origin box — where nginx served the PRIVATE-CA cert. Public visitors
therefore hit a cert error on the disk origin and the boot fails unless they
installed the private CA. The fix: give the origin web server a PUBLIC Let's
Encrypt cert.

## Decisions (all closed)

1. **Facility is opt-in and host-side only.** New switch in `.env`:
   `LETSENCRYPT_EMAIL=<acme email>` (single home:
   `scripts/lib/webvm-common.sh`, default empty = OFF — the private-CA path in
   `scripts/gen-certs.sh` is byte-unchanged when off). When ON,
   `gen-certs.sh` (`make certs` / every `make up`) runs
   `certbot certonly --standalone` and `scripts/le-install.sh` installs the
   lineage as `certs/server.{crt,key}` — the SAME files nginx already mounts,
   so **no compose/nginx/entrypoint change exists**. The private CA is still
   ensured (kept for tooling/E2E trust).
2. **Challenge: HTTP-01 standalone on port 80.** The deployment sits behind
   Cloudflare's Always-HTTPS on `webvm.nedprod.com`, and the user chose to
   keep Cloudflare's API out of it (no DNS-01 token). Every SAN domain must
   therefore resolve DIRECTLY to the box with nothing proxying port 80. Port
   80 is bound only while certbot validates; the stack itself never serves
   plain HTTP (compose publishes 443/8443/… only).
3. **SAN scope (live deployment): `disk.webvm.nedprod.com` ONLY.** The
   original request also named `webvm.nedprod.com`, but that name is
   CF-proxied and cannot be HTTP-01-validated without origin serving changes;
   the user's decision (2026-09-03): issue for the disk host only, no
   Cloudflare involvement. Facility defaults remain general: with
   `LETSENCRYPT_DOMAINS` empty the SAN is derived from the DNS names the
   private cert would carry (a hostname `CONTROL_HOST` + the
   `WEBVM_DISK_BASE_URL` host); an explicit comma/space list overrides
   (deduped). The live box sets `LETSENCRYPT_DOMAINS=disk.webvm.nedprod.com`.
   Adding `webvm.nedprod.com` later = one re-issue (certbot lineage
   `webvm`).
4. **Fail-closed guards** (each with a FATAL + fix hint):
   * Tailnet/LAN deployments (samba/webdav backends, or browser/none with
     `HEADSCALE_ENABLED=1`): the LE cert has no IP SANs (gateway dials
     `172.28.0.10`) and is not signed by `certs/ca.crt` (gateway
     `SSL_CERT_FILE`) — enabling it would break the gateway's TLS.
   * No public DNS names (IP-only `CONTROL_HOST` + no disk host): nothing to
     certify; such deployments keep the private CA.
   * IP literal / `localhost` in the SAN list (LE issues DNS names only).
   * `certbot` not installed (hint: `apt-get install -y certbot`).
5. **Renewal = certbot's own cadence.** The distro `certbot.timer` (enabled
   on the box) re-runs the recorded authenticator + deploy hook; the hook AND
   every `gen-certs.sh` run call `scripts/le-install.sh` (cwd/env-independent:
   re-loads `.env`, resolves the deployment root from its own path), which
   copies `fullchain.pem`/`privkey.pem` → `certs/server.{crt,key}` (only when
   changed) and reloads nginx when the server container is running (a swapped
   file inside the `:ro` mount is not picked up until nginx re-reads it; a
   FAILED reload exits 1 so the running stack's stale-cert state is loud).
   `LETSENCRYPT_NO_RELOAD=1` skips the docker leg (unit-test knob).
6. **Lineage name `webvm` is stable** (`LETSENCRYPT_CERT_NAME`) — a valid
   cert with matching domains makes `certbot certonly` a no-op (~1 s, no CA
   contact), so per-launch `make up` stays cheap. Changing the SAN later
   requires `certbot delete --cert-name webvm` before re-issue (documented in
   `.env.example`).

## Files

`scripts/lib/webvm-common.sh` (+ byte-identical guest copy), `scripts/
gen-certs.sh` (LE branch; disk-host extraction refactored into
`webvm_disk_host` shared with the private SAN), `scripts/le-install.sh` (new),
`.env.example`, `README.md`, `tests/unit/test_scripts.py` (defaults-off pin,
derivation/override/dedupe via a stub certbot, fail-closed guards, disk-host
private-SAN regression). Unit suite: 287 passed.

## Live enablement record (webvm.nedprod.com origin, 82.47.22.78)

Done 2026-09-03 (commit 9b183e8 shipped to the box as a git bundle — GitHub
push from the Mac was restored later the same day; the box then re-verified
in sync with GitHub origin/main via a plain-https `git pull --ff-only`, 0
ahead/behind, no stored credentials):

- Box `.env` (never committed): `LETSENCRYPT_EMAIL=s_letsencrypt@nedprod.com`,
  `LETSENCRYPT_DOMAINS=disk.webvm.nedprod.com` (explicit single-SAN override —
  `webvm.nedprod.com` stays CF-proxied and unlisted; CF's edge cert continues
  to serve the main site, and the CF→origin leg works because the zone's SSL
  mode is Full, not strict).
- `apt-get install certbot` (2.9.0) → `certbot.timer` enabled + active
  (twice-daily renewals).
- `sh scripts/gen-certs.sh` issued the cert: account registered, HTTP-01
  standalone on port 80 (free — compose publishes 443/8443/8082/3478 only),
  deploy hook `scripts/le-install.sh` copied the lineage into `certs/` and
  reloaded the RUNNING server container (no stack restart, no rebuild — the
  facility is host-side only).
- Verified from a public vantage: `disk.webvm.nedprod.com` now presents
  `CN=disk.webvm.nedprod.com`, SAN `DNS:disk.webvm.nedprod.com`, issuer
  Let's Encrypt, valid 2026-09-03 → 2026-12-02; nginx answers with public
  trust; ext2 range GET returns 206 + `access-control-allow-origin: *` +
  `content-range` — the split-brain disk-read path is now public-cert clean.
  `webvm.nedprod.com` through Cloudflare unaffected.
- Renewal verified: `certbot renew --dry-run` simulated a successful renewal
  (note: non-interactive renewals sleep a random delay of up to ~390 s before
  simulating — that is normal certbot behavior, not a hang; the deploy hook
  is recorded in `/etc/letsencrypt/renewal/webvm.conf` as
  `renew_hook = /root/webbrowser-python-idle/scripts/le-install.sh`).
- The host cron `reset-cycle.sh` (6-hourly) rebuilds/restarts on upstream
  changes and re-runs `make up` → gen-certs.sh → certbot (no-op while valid);
  the deploy hook covers certbot's own renewals between cycles.
- Caveat kept on record: if the zone's CF SSL mode is ever set to Full
  (strict), the CF→origin fetch of webvm.nedprod.com would reject the
  disk-only origin cert — keep Full, or add webvm.nedprod.com to
  LETSENCRYPT_DOMAINS and give the origin an HTTP-01 pass-through for the
  proxied name.
