import { expect, test } from '../lib/browser.js';
import { waitForDesktop, lightRatio } from '../lib/desktop.js';
import { basicAuthHeaders } from '../lib/webdav-auth.js';

// Root-visit tailnet regression (plan §9.4): the user's exact acceptance
// sequence — open https://127.0.0.1:8081 (the ROOT: 302 -> /alpine.html, the
// baked /webvm-config.js auto-wires the tailnet, NO hash URL), let the file
// manager load, then verify the guest data path reaches the gateway relay —
// the in-guest `nc -z <GATEWAY_TAILNET_IP> <WEBDAV_PORT>` must SUCCEED, not
// hang. The guest's connect(2) is served by the page-side cjTailscale socket
// adapter (networking-bug.md §16), so the test:
//   1. waits for the sync agent's webvm.lock on the backend — the in-guest
//      wait_for_tailnet + lease PUT only completes when the guest itself can
//      reach the backend (a broken data path hangs here, exactly like nc);
//   2. then drives the SAME socket adapter a guest connect(2) uses (the twin
//      of `nc -z`) and asserts a full TCP connect + HTTP round-trip to the
//      gateway's WebDAV relay.
// Requires the webdav CI phase env (E2E_GATEWAY_IP + E2E_WEBDAV_*) — self-
// skips otherwise, like sync.spec.js.

const GATEWAY_IP = process.env.E2E_GATEWAY_IP || '';
const WEBDAV_BASE = process.env.E2E_WEBDAV_BASE;
const WEBDAV_USER = process.env.E2E_WEBDAV_USER || '';
const WEBDAV_PASS = process.env.E2E_WEBDAV_PASS || '';
const WEBDAV_PORT = Number(process.env.E2E_WEBDAV_PORT || 8082);

const authHeaders = basicAuthHeaders(WEBDAV_USER, WEBDAV_PASS);

const enabled = Boolean(GATEWAY_IP && WEBDAV_BASE && WEBDAV_USER && WEBDAV_PASS);

test.skip(!enabled, 'network spec needs E2E_GATEWAY_IP + E2E_WEBDAV_* env (webdav CI phase)');

test('root visit: desktop loads, then the guest data path reaches the gateway relay (nc -z sequence)', async ({
	page,
	request,
}) => {
	test.setTimeout(600_000);

	// The exact user sequence: visit the site ROOT (no hash URL anywhere).
	await page.goto('/', { waitUntil: 'domcontentloaded' });

	// The root 302s to /alpine.html, and the baked config must have seeded
	// the session WITHOUT any URL hash (no explicit session, no #authKey).
	expect(page.url()).toMatch(/\/alpine\.html$/);
	expect(page.url()).not.toMatch(/#/);

	// "I let the file manager load" — the desktop is up and the explorer's
	// light window fills the canvas.
	await waitForDesktop(page);
	await expect
		.poll(() => lightRatio(page), { timeout: 120_000, intervals: [3000] })
		.toBeGreaterThan(0.35);

	// The guest's sync agent only PUTs webvm.lock after its wait_for_tailnet
	// succeeds — the in-guest data path works end to end (guest -> tailnet ->
	// gateway relay -> backend). If the data path is broken, this poll times
	// out exactly like the user's hanging `nc -z`.
	//
	// Fresh-start the lease first: a lock left behind by a crashed session
	// would make the poll pass without THIS boot's agent ever writing it.
	// After the delete, the lock can only reappear via a lease PUT — either
	// this VM's agent or a still-running session's heartbeat — and any of
	// those requires a working guest data path.
	await request.delete(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders });
	await expect
		.poll(
			async () => (await request.get(WEBDAV_BASE + 'webvm.lock', { headers: authHeaders })).ok(),
			{ timeout: 240_000, intervals: [5000] }
		)
		.toBe(true);

	// The nc twin: drive the SAME cjTailscale socket wrapper the CheerpX core
	// hands guest connect(2) syscalls to (networking-bug.md §16) — TCP connect
	// to the gateway's WebDAV relay, then a raw HTTP round-trip.
	await page.waitForFunction(
		() => window.cjTailscaleSocket && window.cjTailscaleParseIp,
		{ timeout: 60_000 }
	);
	const probe = await page.evaluate(
		async ({ gatewayIp, port }) => {
			const out = { syn: false, http: false, head: '', err: null };
			try {
				const sock = new window.cjTailscaleSocket();
				const ip = window.cjTailscaleParseIp(gatewayIp);
				sock.bind(0);
				sock.connect(ip, port);
				await Promise.race([
					sock.waitOutgoing().then(() => {
						out.syn = true;
					}),
					new Promise((_, rej) =>
						setTimeout(
							() => rej(new Error('TCP connect (waitOutgoing) timed out — the guest nc -z hangs here')),
							30_000
						)
					),
				]);
				// Minimal HTTP/1.1 GET through the raw socket (a 401 without
				// Basic auth still proves the TCP + HTTP round-trip).
				const req =
					'GET /webdav/ HTTP/1.1\r\nHost: ' +
					gatewayIp +
					':' +
					port +
					'\r\nUser-Agent: e2e-network-spec\r\nConnection: close\r\n\r\n';
				const bytes = new TextEncoder().encode(req);
				let off = 0;
				while (off < bytes.length) {
					const n = sock.send(bytes, off, bytes.length - off);
					if (n > 0) {
						off += n;
						continue;
					}
					if (n === -11) {
						await new Promise((r) => setTimeout(r, 100));
						continue;
					}
					throw new Error('send failed rc=' + n);
				}
				const decoder = new TextDecoder('latin1');
				const t1 = Date.now();
				while (Date.now() - t1 < 15_000) {
					const buf = new Uint8Array(4096);
					const n = sock.recv(buf, 0, buf.length, 0);
					if (n > 0) {
						out.head += decoder.decode(buf.slice(0, n));
						if (out.head.includes('\r\n\r\n')) break;
						continue;
					}
					if (n === -11) {
						await new Promise((r) => setTimeout(r, 200));
						continue;
					}
					break;
				}
				out.http = out.head.includes('HTTP/1.1');
				try {
					sock.close();
				} catch (e) {}
			} catch (e) {
				out.err = String((e && e.stack) || e);
			}
			return out;
		},
		{ gatewayIp: GATEWAY_IP, port: WEBDAV_PORT }
	);

	expect(probe.err, probe.err || 'adapter probe threw').toBeNull();
	expect(probe.syn, 'TCP connect to the gateway relay must complete (the guest nc -z twin)').toBe(true);
	expect(probe.http, 'HTTP round-trip through the relay must complete — got: ' + probe.head).toBe(true);

	// The LISTEN twin: guest bind(2)/listen(2) are handed to the interface's
	// TCPServerSocket — the page MUST implement it, or the CheerpX core's
	// dispatcher crashes with "TCPServerSocket is not a function" the moment
	// any guest process binds (busybox nc always binds before connecting —
	// `nc -z` crashed the worker before this method existed). Exercise bind+
	// listen only (the §15 crash regression).
	//
	// The ACCEPT path is deliberately NOT asserted: verified 2026-08-16, the
	// REBUILT tailscale.wasm consumes inbound TCP for the node's own IP
	// internally (its PeerAPI self-loop — "initPeerAPIListener"), so a SYN
	// from a real peer (gateway) never reaches the tun and the IpStack spins
	// a SYN/SYNACK retransmission loop — guest servers can bind+listen but
	// never accept (see plans/networking-bug.md §16.9). That is a runtime
	// limitation, not a page-side bug; a page-side accept assertion can only
	// fail spuriously.
	await page.waitForFunction(
		() => window.cjTailscaleAdapter,
		{ timeout: 60_000 }
	);
	const listenProbe = await page.evaluate(
		async ({ port }) => {
			const out = { listened: false, err: null };
			try {
				const srv = window.cjTailscaleAdapter.TCPServerSocket('0.0.0.0', { localPort: port });
				if (!srv) throw new Error('TCPServerSocket unavailable (tun not ready)');
				await Promise.race([
					srv.opened,
					new Promise((_, rej) =>
						setTimeout(() => rej(new Error('TCPServerSocket opened timed out')), 30_000)
					),
				]);
				out.listened = true;
				try { await srv.close(); } catch (e) {}
			} catch (e) {
				out.err = String((e && e.stack) || e);
			}
			return out;
		},
		{ port: 38083 }
	);

	expect(listenProbe.err, listenProbe.err || 'TCPServerSocket probe threw').toBeNull();
	expect(listenProbe.listened, 'interface TCPServerSocket must bind+listen without crashing (the guest nc -z crash)').toBe(true);
});
