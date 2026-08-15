// Stream-path probe: drives the REAL network.js socket adapter
// (window.cjTailscaleAdapter.TCPSocket — the same wrapper the CheerpX core
// uses) and pushes a ~10KB HTTP PUT through its streams — the path the
// guest's snapshot upload uses. Exercises the shipped wrapper instead of a
// copied implementation (networking-bug.md §16).
// Usage: PREAUTH_KEY=... GATEWAY_IP=100.64.0.1 node stream-put-probe.mjs
import { chromium } from 'playwright';

const KEY = process.env.PREAUTH_KEY || '';
const GATEWAY_IP = process.env.GATEWAY_IP || '100.64.0.1';
const CONTROL_HOST = process.env.CONTROL_HOST || 'host.docker.internal';
const PORT = Number(process.env.WEBDAV_PORT || 8082);
const BODY_SIZE = Number(process.env.BODY_SIZE || 12000);

if (!KEY) { console.error('PREAUTH_KEY is required'); process.exit(2); }

const SESSION_URL =
	'https://' + CONTROL_HOST + ':8081/alpine.html#authKey=' + KEY +
	'&controlUrl=https://' + CONTROL_HOST + ':8443' +
	'&syncUrl=http://' + GATEWAY_IP + ':' + PORT + '/webdav/' +
	'&syncUser=webdav&syncPass=webdavpass';

const browser = await chromium.launch({ args: ['--host-resolver-rules=MAP ' + CONTROL_HOST + ' 127.0.0.1'] });
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();
page.on('console', (m) => { const t = m.text(); if (/prctl|SYS_SETSOCKOPT|\/dev\/kmsg|oom_score|logtail|derp\/probe|NOTIFY/.test(t)) return; console.log('CONSOLE ' + m.type() + ': ' + t.slice(0, 180)); });

await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForFunction(() => window.cjTailscaleSocket && window.cjTailscaleParseIp, { timeout: 60000 }).catch(() => {});
await page.waitForTimeout(8000);

const result = await page.evaluate(async ({ gatewayIp, port, bodySize }) => {
	const out = { steps: [], err: null };
	const step = (s) => { out.steps.push(s); console.log('[PROBE] ' + s); };
	try {
		// --- the real wrapper the core uses (network.js TCPSocket) ---
		if (!window.cjTailscaleAdapter || !window.cjTailscaleAdapter.TCPSocket) {
			throw new Error('cjTailscaleAdapter not exposed (tailnet driver not up?)');
		}
		const wrapper = window.cjTailscaleAdapter.TCPSocket(gatewayIp, port);
		if (!wrapper) throw new Error('TCPSocket returned null (no tun exports)');
		step('wrapper created, awaiting opened');
		const { readable: rd, writable: wr } = await Promise.race([
			wrapper.opened,
			new Promise((_, rej) => setTimeout(() => rej(new Error('opened timeout 30s')), 30000)),
		]);
		step('opened resolved');
		const writer = wr.getWriter();
		const body = 'x'.repeat(bodySize);
		const headers =
			'PUT /webdav/_stream-put-probe.bin HTTP/1.1\r\n' +
			'Host: ' + gatewayIp + ':' + port + '\r\n' +
			'Content-Length: ' + body.length + '\r\n' +
			'Connection: close\r\n\r\n';
		step('writing headers (' + headers.length + 'B)');
		await writer.write(new TextEncoder().encode(headers));
		step('headers written, writing body (' + body.length + 'B)');
		const t0 = Date.now();
		await Promise.race([
			writer.write(new TextEncoder().encode(body)),
			new Promise((_, rej) => setTimeout(() => rej(new Error('body write timeout 30s')), 30000)),
		]);
		out.bodyMs = Date.now() - t0;
		step('body written in ' + out.bodyMs + 'ms, closing writable');
		await Promise.race([
			writer.close(),
			new Promise((_, rej) => setTimeout(() => rej(new Error('close timeout 10s')), 10000)),
		]);
		step('writable closed, reading response');
		const reader = rd.getReader();
		const chunks = [];
		const decoder = new TextDecoder('latin1');
		const t1 = Date.now();
		try {
			while (Date.now() - t1 < 20000) {
				const { value, done } = await Promise.race([
					reader.read(),
					new Promise((_, rej) => setTimeout(() => rej(new Error('read timeout 20s')), 20000)),
				]);
				if (done) { step('read done (EOF)'); break; }
				chunks.push(decoder.decode(value));
				step('recv ' + value.length + ' (total ' + chunks.join('').length + ')');
				if (chunks.join('').includes('\r\n\r\n')) break;
			}
		} catch (e) { step('read error: ' + e); }
		out.resp = chunks.join('').slice(0, 200);
		step('response head: ' + JSON.stringify(out.resp.slice(0, 120)));
		try { wrapper.close(); } catch (e) {}
	} catch (e) {
		out.err = String(e && e.stack || e);
		step('FAILED: ' + out.err);
	}
	return out;
}, { gatewayIp: GATEWAY_IP, port: PORT, bodySize: BODY_SIZE });

console.log('\n==== RESULT ====');
for (const s of result.steps) console.log('  ' + s);
console.log('bodyMs=' + result.bodyMs + ' err=' + (result.err || 'none'));
console.log('resp=' + JSON.stringify((result.resp || '').slice(0, 150)));
await browser.close();
process.exit(result.resp && result.resp.includes('HTTP/1.1') ? 0 : 1);
