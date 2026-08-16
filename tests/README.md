# Tests

Layered suite per `plans/webvm_implementation.md` §9 — fast deterministic
checks first, a real-browser E2E as the gate, plus a LAN acceptance script
for what CI cannot reach.

```
tests/
├─ unit/            pytest (sync agent logic + transport, template rendering,
│                    script hygiene) — seconds, no network, no Docker guest
├─ rootfs/          smoke tests against the built guest image (docker run)
├─ server/          integration against a booted stack (headers, ext2 ranges,
│                    WebDAV round-trip, headscale join test)
├─ e2e/             Playwright: real VM boot in headless Chromium
├─ fixtures/        fake WebDAV server, fake home, test data
```

## Order

1. Unit: `make test-unit` (or `docker compose --profile test run --rm test-unit`)
2. Build: `make build` (guest ext2 + frontend + images)
3. Rootfs smoke: `tests/rootfs/smoke.sh browser` (repeat per backend:
   `samba`, `webdav`, `none`)
4. Bring up the stack: `make up` (browser mode) or
   `make up-tailnet` with a webdav `.env` for the sync/control tests
5. Server integration: `STORAGE_BACKEND=webdav tests/server/integration.sh`
   (needs `HEADSCALE_PREAUTHKEY`/`GATEWAY_AUTHKEY` bootstrapped — see
   `make url` / the README bootstrap section)
6. E2E (browser mode): `cd tests/e2e && npm ci && npx playwright install chromium`
   then `E2E_SITE_URL=https://127.0.0.1:8081/alpine.html npx playwright test`
7. E2E (webdav mode): pass the full session hash URL plus the gateway's
   tailnet IP (the `network` spec boots the site ROOT — baked
   `/webvm-config.js` — and verifies the guest data path reaches the gateway
   relay, the `nc -z` sequence):
   `E2E_WEBDAV_URL='<full hash URL from make url>' E2E_GATEWAY_IP=<gateway-tailnet-ip> E2E_WEBDAV_BASE=http://127.0.0.1:8082/webdav/ E2E_WEBDAV_USER=… E2E_WEBDAV_PASS=… npx playwright test`

## LAN acceptance

`make acceptance` runs `scripts/acceptance.sh` — the manual/LAN checklist that
CI cannot cover (browser CA trust, relay reachability from the guest, Samba
share connect, no-internet proofs).
