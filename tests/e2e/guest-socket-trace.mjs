// Guest-socket trace v6 (2026-08-16): confirms the cheerpOSNetInit fix.
// Wraps the raw cjTailscaleSocket constructor AND re-wraps whenever
// cheerpOSNetInit reassigns it, then fires cheerpOSNetInit after the driver
// is up. If the guest switches to the raw path, raw calls become visible and
// webvm.lock appears on the backend.
//
// Usage: PREAUTH_KEY=... GATEWAY_IP=100.64.0.1 node guest-socket-trace.mjs
import { chromium } from 'playwright';

const KEY = process.env.PREAUTH_KEY || '';
const GATEWAY_IP = process.env.GATEWAY_IP || '100.64.0.1';
const CONTROL_HOST = process.env.CONTROL_HOST || '127.0.0.1';
const PORT = Number(process.env.WEBDAV_PORT || 8082);
const WATCH_S = Number(process.env.WATCH_S || 130);

if (!KEY) { console.error('PREAUTH_KEY is required'); process.exit(2); }

const SESSION_URL =
	'https://' + CONTROL_HOST + ':8081/alpine.html#authKey=' + KEY +
	'&controlUrl=https://' + CONTROL_HOST + ':8443' +
	'&syncUrl=http://' + GATEWAY_IP + ':' + PORT + '/webdav/' +
	'&syncUser=webdav&syncPass=webdavpass';

const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();

page.on('pageerror', (e) => console.log('PAGEERROR: ' + e));
page.on('console', (m) => {
	const t = m.text();
	if (/prctl|SYS_SETSOCKOPT|\/dev\/kmsg|oom_score|logtail|NOTIFY|netmap diff|Reconfig done|PollNetMap|initPeerAPIListener|TKA state|mapRoutine|sendStatus|successful lite map|control: \[v|HostInfo|authRoutine|creating new noise|No AuthURL|restartMap|UDP bind|Routine:|Interface state|disco key|SetPrivateKey|endpoints changed|adding connection|active derp|connecting websocket|NetInfo|client.newEndpoints|home DERP|home is now|netcheck|wg: \[v2\]|magicsock|health/.test(t)) return;
	if (/\[WRAP\]/.test(t)) return;
	console.log('CONSOLE ' + t.slice(0, 240));
});

await page.addInitScript(() => {
	window.__st = { raw: 0, rawAfter: 0, adapter: 0 };
	let lastCtor = null;
	const wrapRaw = (label) => {
		const c = window.cjTailscaleSocket;
		if (typeof c !== 'function' || c === lastCtor || c.__wrapped) return;
		lastCtor = c;
		const W = function (...a) {
			window.__st.raw++;
			if (label === 'post') window.__st.rawAfter++;
			console.log('[WRAP] RAW socket (' + label + ') total=' + window.__st.raw);
			return new c(...a);
		};
		W.Eagain = c.Eagain; W.__wrapped = true;
		window.cjTailscaleSocket = W;
		console.log('[WRAP] raw wrapped (' + label + ')');
	};
	const wrapAdapter = () => {
		const a = window.cjTailscaleAdapter;
		if (!a || !a.TCPSocket || a.__wrapped) return;
		const orig = a.TCPSocket.bind(a);
		a.TCPSocket = (...args) => {
			window.__st.adapter++;
			console.log('[WRAP] adapter TCPSocket total=' + window.__st.adapter);
			return orig(...args);
		};
		a.__wrapped = true;
		console.log('[WRAP] adapter wrapped');
	};
	window.__wrap = setInterval(() => {
		wrapRaw('pre');
		wrapRaw('post');   // re-wrap after cheerpOSNetInit reassigns the global
		wrapAdapter();
	}, 200);
});

console.log('open', SESSION_URL.slice(0, 70) + '…');
await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });

await page.waitForFunction(() => window.__st.raw > 0 || (window.cjTailscaleSocket && window.cjTailscaleSocket.__wrapped), { timeout: 60000 }).catch(() => {});
await page.waitForTimeout(8000);
const fired = await page.evaluate(({ controlUrl, authKey }) => {
	if (typeof window.cheerpOSNetInit !== 'function') return 'cheerpOSNetInit MISSING';
	try {
		window.cheerpOSNetInit(
			'/cheerpx/tun/tailscale_tun_auto.js',
			() => {},
			authKey,
			controlUrl,
			null, {},
			() => {},
			() => console.log('[WRAP] cheerpOSNetInit cb CALLED')
		);
		return 'fired';
	} catch (e) { return 'THREW: ' + e; }
}, { controlUrl: 'https://' + CONTROL_HOST + ':8443', authKey: KEY });
console.log('*** cheerpOSNetInit: ' + fired + ' ***');

const t0 = Date.now();
while (Date.now() - t0 < WATCH_S * 1000) {
	await page.waitForTimeout(3000);
}
const fin = await page.evaluate(() => window.__st || {});
console.log('=== END: raw=' + fin.raw + ' rawAfter=' + fin.rawAfter + ' adapter=' + fin.adapter + ' ===');
await browser.close();
