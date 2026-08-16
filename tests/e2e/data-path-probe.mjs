// Browser-side data-path probe (networking-bug.md §16): drives the same
// socket path the CheerpX core uses for guest TCP connects — the app driver's
// cjTailscale* globals (IpStack TCP socket -> tun MessageChannel -> wgengine
// -> DERP -> gateway) — directly from the page, and reports whether a TCP
// connection to the gateway's WebDAV (100.64.0.1:8082) completes.
//
// Usage: PREAUTH_KEY=... GATEWAY_IP=100.64.0.1 node data-path-probe.mjs
import { chromium } from 'playwright';

const KEY = process.env.PREAUTH_KEY || '';
const GATEWAY_IP = process.env.GATEWAY_IP || '100.64.0.1';
const CONTROL_HOST = process.env.CONTROL_HOST || '127.0.0.1';
const PORT = Number(process.env.WEBDAV_PORT || 8082);

if (!KEY) { console.error('PREAUTH_KEY is required'); process.exit(2); }

const SESSION_URL =
	'https://' + CONTROL_HOST + ':8081/alpine.html#authKey=' + KEY +
	'&controlUrl=https://' + CONTROL_HOST + ':8443' +
	'&syncUrl=http://' + GATEWAY_IP + ':' + PORT + '/webdav/' +
	'&syncUser=webdav&syncPass=webdavpass';

const browser = await chromium.launch({
});
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();

page.on('console', (m) => {
	const t = m.text();
	if (/prctl|SYS_SETSOCKOPT|\/dev\/kmsg|oom_score|logtail|derp\/probe|NOTIFY/.test(t)) return;
	console.log('CONSOLE ' + m.type() + ': ' + t.slice(0, 220));
});
page.on('pageerror', (e) => console.log('PAGEERROR: ' + e));

console.log('open', SESSION_URL.slice(0, 70) + '…');
await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });

// Wait for the tailnet to reach Running (state 6): the driver wires
// stateUpdateCb which flips networkData.connectionState to CONNECTED, but
// easiest is to poll for the wgengine Running state via the wasm's own
// console output OR just wait until the cjTailscale globals exist and a
// netmap arrived (10-15s is plenty — the old flow reached Running in ~2s).
await page.waitForFunction(() => window.cjTailscaleSocket && window.cjTailscaleParseIp, { timeout: 60000 }).catch(() => {});
await page.waitForTimeout(8000);

const result = await page.evaluate(async ({ gatewayIp, port }) => {
	const out = { steps: [], err: null };
	const step = (s) => { out.steps.push(s); console.log('[PROBE] ' + s); };
	try {
		const sock = new window.cjTailscaleSocket();
		const ip = window.cjTailscaleParseIp(gatewayIp);
		step('socket created, parseIP=' + ip);
		const bc = sock.bind(0);
		step('bind rc=' + bc);
		const rc = sock.connect(ip, port);
		step('connect rc=' + rc);
		const t0 = Date.now();
		await Promise.race([
			sock.waitOutgoing().then(() => { out.syn = 'COMPLETED in ' + (Date.now() - t0) + 'ms'; step('waitOutgoing RESOLVED'); }),
			new Promise((_, rej) => setTimeout(() => rej(new Error('waitOutgoing timeout 30s')), 30000)),
		]);
		out.syn = 'COMPLETED in ' + (Date.now() - t0) + 'ms';
		step('syn completed, sending HTTP request');
		// Minimal HTTP/1.1 GET through the raw socket.
		const req = 'GET /webdav/ HTTP/1.1\r\nHost: ' + gatewayIp + ':' + port + '\r\nUser-Agent: datapath-probe\r\nConnection: close\r\n\r\n';
		const bytes = new TextEncoder().encode(req);
		let off = 0;
		while (off < bytes.length) {
			const n = sock.send(bytes, off, bytes.length - off);
			if (n > 0) { off += n; continue; }
			if (n === -11) { await new Promise((r) => setTimeout(r, 100)); continue; }
			throw new Error('send failed rc=' + n);
		}
		step('sent ' + off + ' bytes');
		const chunks = [];
		const decoder = new TextDecoder('latin1');
		const t1 = Date.now();
		while (Date.now() - t1 < 15000) {
			const buf = new Uint8Array(4096);
			const n = sock.recv(buf, 0, buf.length, 0);
			if (n > 0) {
				chunks.push(decoder.decode(buf.slice(0, n)));
				step('recv ' + n + ' bytes (total ' + chunks.join('').length + ')');
				if (chunks.join('').includes('HTTP/1.1')) { step('HTTP response header seen'); }
				if (chunks.join('').includes('\r\n\r\n')) break;
				continue;
			}
			if (n === -11) { await new Promise((r) => setTimeout(r, 200)); continue; }
			break;
		}
		out.recv = chunks.join('').slice(0, 300);
		step('recv total=' + out.recv.length + ' head=' + JSON.stringify(out.recv.slice(0, 120)));
		try { sock.close(); } catch (e) {}
	} catch (e) {
		out.err = String(e && e.stack || e);
		step('FAILED: ' + out.err);
	}
	return out;
}, { gatewayIp: GATEWAY_IP, port: PORT });

console.log('\n==== PROBE RESULT ====');
console.log('steps:');
for (const s of result.steps) console.log('  ' + s);
console.log('syn=' + (result.syn || 'never completed') + ' err=' + (result.err || 'none'));
console.log('recv=' + JSON.stringify((result.recv || '').slice(0, 200)));

// Also check whether the guest's own attempts landed on WebDAV (count hits
// is done server-side; here just report).
await browser.close();
process.exit(String(result.syn).startsWith('COMPLETED') ? 0 : 1);
