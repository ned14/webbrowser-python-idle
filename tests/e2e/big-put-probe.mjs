// Large-payload transfer probe: does a ~10KB HTTP PUT body flow through
// IpStack -> tun -> wg -> DERP -> gateway (raw socket, no core involved)?
// Usage: PREAUTH_KEY=... GATEWAY_IP=100.64.0.1 node big-put-probe.mjs
import { chromium } from 'playwright';

const KEY = process.env.PREAUTH_KEY || '';
const GATEWAY_IP = process.env.GATEWAY_IP || '100.64.0.1';
const CONTROL_HOST = process.env.CONTROL_HOST || '127.0.0.1';
const PORT = Number(process.env.WEBDAV_PORT || 8082);
const BODY_SIZE = Number(process.env.BODY_SIZE || 12000);

if (!KEY) { console.error('PREAUTH_KEY is required'); process.exit(2); }

const SESSION_URL =
	'https://' + CONTROL_HOST + ':8081/alpine.html#authKey=' + KEY +
	'&controlUrl=https://' + CONTROL_HOST + ':8443' +
	'&syncUrl=http://' + GATEWAY_IP + ':' + PORT + '/webdav/' +
	'&syncUser=webdav&syncPass=webdavpass';

const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();
page.on('console', (m) => { const t = m.text(); if (/prctl|SYS_SETSOCKOPT|\/dev\/kmsg|oom_score|logtail|derp\/probe|NOTIFY/.test(t)) return; console.log('CONSOLE ' + m.type() + ': ' + t.slice(0, 180)); });
page.on('pageerror', (e) => console.log('PAGEERROR: ' + e));

console.log('open…');
await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForFunction(() => window.cjTailscaleSocket && window.cjTailscaleParseIp, { timeout: 60000 }).catch(() => {});
await page.waitForTimeout(8000);

const result = await page.evaluate(async ({ gatewayIp, port, bodySize }) => {
	const out = { steps: [], err: null };
	const step = (s) => { out.steps.push(s); console.log('[PROBE] ' + s); };
	const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
	try {
		const sock = new window.cjTailscaleSocket();
		const ip = window.cjTailscaleParseIp(gatewayIp);
		sock.bind(0);
		if (sock.connect(ip, port) !== 0) throw new Error('connect failed');
		await sock.waitOutgoing();
		step('connected');

		const body = 'x'.repeat(bodySize);
		const headers =
			'PUT /webdav/_big-put-probe.bin HTTP/1.1\r\n' +
			'Host: ' + gatewayIp + ':' + port + '\r\n' +
			'Content-Length: ' + body.length + '\r\n' +
			'User-Agent: big-put-probe\r\n' +
			'Connection: close\r\n\r\n';
		const all = new TextEncoder().encode(headers + body);
		step('sending ' + all.length + ' bytes total');
		let off = 0;
		let spins = 0;
		const t0 = Date.now();
		while (off < all.length) {
			const n = sock.send(all, off, all.length - off);
			if (n > 0) { off += n; spins = 0; continue; }
			if (n === -11) { spins++; if (spins % 500 === 0) step('EAGAIN spin ' + spins + ' at +' + (Date.now() - t0) + 'ms (sent ' + off + '/' + all.length + ')'); await sleep(5); continue; }
			throw new Error('send failed rc=' + n + ' at off=' + off);
		}
		out.sent = all.length;
		step('sent all ' + off + ' bytes in ' + (Date.now() - t0) + 'ms');

		// read the response
		const chunks = [];
		const decoder = new TextDecoder('latin1');
		const t1 = Date.now();
		while (Date.now() - t1 < 20000) {
			const buf = new Uint8Array(4096);
			const n = sock.recv(buf, 0, buf.length, 0);
			if (n > 0) {
				chunks.push(decoder.decode(buf.slice(0, n)));
				step('recv ' + n + ' (total ' + chunks.join('').length + ')');
				if (chunks.join('').includes('\r\n\r\n')) break;
				continue;
			}
			if (n === -11) { await sleep(200); continue; }
			step('recv rc=' + n + ' (closed)');
			break;
		}
		out.resp = chunks.join('').slice(0, 200);
		step('response head: ' + JSON.stringify(out.resp.slice(0, 120)));
		try { sock.close(); } catch (e) {}
	} catch (e) {
		out.err = String(e && e.stack || e);
		step('FAILED: ' + out.err);
	}
	return out;
}, { gatewayIp: GATEWAY_IP, port: PORT, bodySize: BODY_SIZE });

console.log('\n==== RESULT ====');
for (const s of result.steps) console.log('  ' + s);
console.log('sent=' + result.sent + ' err=' + (result.err || 'none'));
console.log('resp=' + JSON.stringify((result.resp || '').slice(0, 150)));
await browser.close();
process.exit(result.resp && result.resp.includes('HTTP/1.1') ? 0 : 1);
