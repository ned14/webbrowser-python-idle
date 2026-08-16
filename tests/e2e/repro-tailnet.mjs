// Instrumented tailnet repro (networking-bug.md §4a + T10).
//
// Captures EVERY console line + pageerror with timestamps (to find the FIRST
// error before the runtime wedge, per webvm #222), every control-plane
// request/response, and WebSocket handshakes. Polls the WebDAV backend for
// webvm.lock. Exits 0 only if the lock appears.
//
// Usage:
//   PREAUTH_KEY=... GATEWAY_IP=100.64.0.1 node repro-tailnet.mjs
//
// Env:
//   PREAUTH_KEY  — headscale preauth key (required)
//   GATEWAY_IP   — gateway tailnet IP (default 100.64.0.1)
//   WEBDAV_BASE  — default http://127.0.0.1:8082/webdav/
//   CONTROL_HOST — default 127.0.0.1 (hostnames are banned)
//   DEBUG_REQS   — "1" to log every browser request (not just control plane)
//   LIMIT_S      — how long to watch (default 180)

import { chromium } from 'playwright';
import { basicAuthHeaders } from './lib/webdav-auth.js';

const KEY = process.env.PREAUTH_KEY || '';
const GATEWAY_IP = process.env.GATEWAY_IP || '100.64.0.1';
const CONTROL_HOST = process.env.CONTROL_HOST || '127.0.0.1';
const WEBDAV_BASE = process.env.WEBDAV_BASE || 'http://127.0.0.1:8082/webdav/';
const AUTH = { username: process.env.WEBDAV_USER || 'webdav', password: process.env.WEBDAV_PASS || 'webdavpass' };
// Playwright's APIRequestContext `auth` option does not send Basic auth on
// plain-HTTP requests (returns 401); send the header explicitly.
const AUTH_HEADER = basicAuthHeaders(AUTH.username, AUTH.password);
const LIMIT_S = Number(process.env.LIMIT_S || 180);
const DEBUG_REQS = process.env.DEBUG_REQS === '1';

if (!KEY) {
	console.error('PREAUTH_KEY is required');
	process.exit(2);
}

// Public-control mode (T12): omit controlUrl/authKey entirely — the client
// should go into interactive-login and print a login URL.
const PUBLIC_MODE = process.env.PUBLIC_MODE === '1';

let SESSION_URL;
if (PUBLIC_MODE) {
	SESSION_URL = 'https://' + CONTROL_HOST + ':8081/alpine.html';
} else {
	SESSION_URL =
		'https://' + CONTROL_HOST + ':8081/alpine.html#authKey=' + KEY +
		'&controlUrl=https://' + CONTROL_HOST + ':8443' +
		'&syncUrl=http://' + GATEWAY_IP + ':8082/webdav/' +
		'&syncUser=webdav&syncPass=webdavpass';
}

const browser = await chromium.launch({
	...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
});
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();

// In-page tracing: unhandled rejections (never show as console messages in
// some paths), every fetch() with its URL, and wasm instantiations. This is
// how we find where the autoConf chain hangs (tailscale.wasm is never fetched).
await page.addInitScript(() => {
	window.addEventListener('unhandledrejection', (e) => {
		console.log('[TRACE] unhandledrejection: ' + (e.reason && (e.reason.stack || e.reason.message || e.reason)));
	});
	const origFetch = window.fetch.bind(window);
	window.fetch = async (...args) => {
		const url = typeof args[0] === 'string' ? args[0] : args[0]?.url;
		console.log('[TRACE] fetch start ' + url);
		const t0 = performance.now();
		try {
			const r = await origFetch(...args);
			console.log('[TRACE] fetch done ' + url + ' -> ' + r.status + ' (' + Math.round(performance.now() - t0) + 'ms)');
			return r;
		} catch (e) {
			console.log('[TRACE] fetch FAIL ' + url + ' ' + e);
			throw e;
		}
	};
	const origInst = WebAssembly.instantiate.bind(WebAssembly);
	WebAssembly.instantiate = (buf, imports) => {
		console.log('[TRACE] instantiate ' + (buf?.byteLength || '?') + ' bytes');
		return origInst(buf, imports);
	};
	const origIS = WebAssembly.instantiateStreaming.bind(WebAssembly);
	WebAssembly.instantiateStreaming = (src, imports) => {
		console.log('[TRACE] instantiateStreaming ' + (src?.url || src));
		return origIS(src, imports);
	};
	// Capture blob module contents (the runtime creates blob wrappers that
	// drive the tun modules).
	const origCreate = URL.createObjectURL.bind(URL);
	window.__blobs = {};
	URL.createObjectURL = (blob) => {
		const url = origCreate(blob);
		try {
			blob.text().then((t) => {
				window.__blobs[url] = t;
				console.log('[TRACE] blobURL ' + url.slice(0, 26) + ' (' + t.length + ' chars) HEAD: ' + t.slice(0, 120).replace(/\n/g, ' ') + ' TAIL: ' + t.slice(-160).replace(/\n/g, ' '));
			});
		} catch (e) { console.log('[TRACE] blobURL read failed ' + e); }
		return url;
	};
	const origWorker = window.Worker;
	window.Worker = function (...args) {
		console.log('[TRACE] new Worker ' + args[0]);
		return new origWorker(...args);
	};
	// Wrap the core's network init entry point once cheerpOS.js loads.
	setInterval(() => {
		if (window.cheerpOSNetInit && !window.cheerpOSNetInit.__wrapped) {
			const orig = window.cheerpOSNetInit;
			window.cheerpOSNetInit = function (...args) {
				console.log('[TRACE] cheerpOSNetInit called path=' + args[0] + ' authKey=' + (args[2] ? 'yes' : 'no') + ' controlUrl=' + args[3] + ' dnsIp=' + args[4] + ' ipMap=' + (args[5] ? 'yes' : 'no') + ' netmapCb=' + (args[6] ? 'yes' : 'no') + ' cb=' + (args[7] ? 'yes' : 'no'));
				try {
					const r = orig.apply(this, args);
					console.log('[TRACE] cheerpOSNetInit returned');
					return r;
				} catch (e) {
					console.log('[TRACE] cheerpOSNetInit THREW ' + e);
					throw e;
				}
			};
			window.cheerpOSNetInit.__wrapped = true;
			console.log('[TRACE] cheerpOSNetInit wrapper installed');
		}
	}, 100);
});

const t0 = Date.now();

// --- runtime version routing: serve /cheerpx/** from the CDN for a different
// version, falling back to the local files on 404/204. Tests whether older
// runtimes drive the tailnet (the 1.3.7/1.3.8 runtimes never fetch
// tailscale.wasm).
if (process.env.RUNTIME_VERSION) {
	const cdn = 'https://cxrtnc.leaningtech.com/' + process.env.RUNTIME_VERSION;
	await page.route('**/cheerpx/**', async (r) => {
		const path = new URL(r.request().url()).pathname.replace(/^\/cheerpx\//, '');
		const resp = await fetch(cdn + '/' + path).catch(() => null);
		if (resp && resp.ok) {
			const body = Buffer.from(await resp.arrayBuffer());
			events.push(`${ts()} CDN-RT ${process.env.RUNTIME_VERSION} ${path} (${body.length}B)`);
			return r.fulfill({ status: 200, contentType: resp.headers.get('content-type') || 'application/octet-stream', body });
		}
		events.push(`${ts()} CDN-RT ${process.env.RUNTIME_VERSION} ${path} MISS -> local`);
		return r.continue();
	});
	console.log('RUNTIME_VERSION: routing /cheerpx/** to CDN ' + process.env.RUNTIME_VERSION);
}

// --- module interception: patch the tun glue with step traces ---
// (tailscale.wasm is never fetched, so the autoConf chain hangs somewhere;
// these traces show exactly where.)
if (process.env.TUN_TRACE === '1') {
	const fs = await import('node:fs');
	const { fileURLToPath } = await import('node:url');
	// Derive the tun directory from THIS script's location instead of a
	// hardcoded macOS path (portable across machines/CI).
	const base = fileURLToPath(new URL('../webvm/cheerpx/tun/', import.meta.url));
	const wasmBytes = fs.readFileSync(base + 'tailscale.wasm');

	// Route tailscale.wasm: any fetch of it (file URL or blob-wrapped) lands
	// here and is visible.
	if (process.env.TUN_WASM_ROUTE === '1') {
		await page.route('**/tun/tailscale.wasm', (r) => {
			events.push(`${ts()} TAILSCALE.WASM REQUESTED (${r.request().url()})`);
			return r.fulfill({
				status: 200,
				contentType: 'application/wasm',
				body: wasmBytes,
			});
		});
	}

	let tunJs = fs.readFileSync(base + 'tailscale_tun.js', 'utf8');
	if (process.env.TUN_TRACE_MODE === 'simple') {
		// run4-style: minimal entry log, no try/catch, no import.meta.
		tunJs = tunJs.replace(
			'const lazyRunIpn = async (conf) => {',
			"console.log('[TUN] lazyRunIpn entered (will fetch tailscale.wasm)');\n\tconsole.log('[TUN] im-url=' + import.meta.url + ' go=' + typeof self.Go);\n\tconst lazyRunIpn = async (conf) => {"
		);
	} else if (process.env.TUN_TRACE_MODE === 'safe') {
		// entry log + try/catch, but NO import.meta / new URL at entry
		tunJs = tunJs.replace(
			'const lazyRunIpn = async (conf) => {',
			'const lazyRunIpn = async (conf) => {' +
				'\n\t\tconsole.log("[TUN] lazyRunIpn entered");' +
				'\n\t\ttry {' +
				'\n\t\t\tconsole.log("[TUN] import.meta.url=" + String(import.meta.url));' +
				'\n\t\t\tconsole.log("[TUN] typeof self.Go=" + typeof self.Go + " typeof WebAssembly.instantiate=" + typeof WebAssembly.instantiate);'
		);
		const instAnchor = 'go.run(instance);';
		tunJs = tunJs.replace(
			instAnchor,
			instAnchor + '\n\t\t\tconsole.log("[TUN] wasm instantiated and go.run called");' +
				'\n\t\t} catch (e) {' +
				'\n\t\t\tconsole.log("[TUN] lazyRunIpn FAILED: " + (e && e.stack || e));' +
				'\n\t\t\tthrow e;' +
				'\n\t\t}'
		);
	} else {
		// Insert a trace block at the start of lazyRunIpn (robust, not regex):
		const anchor = 'const lazyRunIpn = async (conf) => {';
		tunJs = tunJs.replace(
			anchor,
			anchor + '\n\t\tconsole.log("[TUN] lazyRunIpn entered");' +
				'\n\t\ttry {' +
				'\n\t\t\tconsole.log("[TUN] import.meta.url=" + import.meta.url + " -> wasm url " + new URL("tailscale.wasm", import.meta.url));'
		);
		// Close the try/catch around the fetch + instantiate + go.run:
		const instAnchor = 'go.run(instance);';
		tunJs = tunJs.replace(
			instAnchor,
			instAnchor + '\n\t\t\tconsole.log("[TUN] wasm instantiated and go.run called");' +
				'\n\t\t} catch (e) {' +
				'\n\t\t\tconsole.log("[TUN] lazyRunIpn FAILED: " + (e && e.stack || e));' +
				'\n\t\t\tthrow e;' +
				'\n\t\t}'
		);
	}
	tunJs = tunJs.replace('export async function init() {', "console.log('[TUN] init() entered, worker=' + (typeof WorkerGlobalScope !== 'undefined') + ' ' + new Error().stack.split('\\n')[2]);\nexport async function init() {");
	tunJs = tunJs.replace('const {IpStack} = await ipStackAwait();', "console.log('[TUN] ipstack module awaited');\n\tconst {IpStack} = await ipStackAwait();\n\tconsole.log('[TUN] ipstack module resolved');");
	tunJs = tunJs.replace('IpStack.init();', "console.log('[TUN] calling IpStack.init()');\n\tIpStack.init();\n\tconsole.log('[TUN] IpStack.init() done');");
	tunJs = tunJs.replace('ipn = newIPN(conf, {', "console.log('[TUN] newIPN(conf) called');\n\t\tipn = newIPN(conf, {");
	tunJs = tunJs.replace('ipn.run({', "console.log('[TUN] ipn.run() called');\n\t\tipn.run({");
	tunJs = tunJs.replace('ipn.up(conf);', "console.log('[TUN] ipn.up(conf) called');\n\t\t\tipn.up(conf);");
	let autoJs = fs.readFileSync(base + 'tailscale_tun_auto.js', 'utf8');
	if (process.env.TUN_TRACE_MODE === 'simple') {
		autoJs = autoJs.replace('export async function autoConf(', "console.log('[AUTO] autoConf called');\nexport async function autoConf(");
	} else {
		autoJs = autoJs.replace('export async function autoConf(', "console.log('[AUTO] autoConf called, worker=' + (typeof WorkerGlobalScope !== 'undefined') + '\\n' + new Error().stack);\nexport async function autoConf(");
	}
	autoJs = autoJs
		.replace('const { tcpSocket, udpSocket, parseIP, dumpIP, resolve, up, down, login, logout, listeners } = await init();', "console.log('[AUTO] init() resolved');\n\tconst { tcpSocket, udpSocket, parseIP, dumpIP, resolve, up, down, login, logout, listeners } = await init();\n\tconsole.log('[AUTO] wiring listeners');")
		.replace('listeners.onstateupdate = (state) => {', "listeners.onstateupdate = (state) => {\n\t\tconsole.log('[AUTO] onstateupdate state=' + state);")
		.replace('\treturn {\n\t\ttcpSocket,', "console.log('[AUTO] autoConf returning exports');\n\treturn {\n\t\ttcpSocket,");
	if (process.env.TUN_TRACE_MODE === 'rich') {
		// Return the FULL export surface (down/login/logout) and log up() calls
		// — if the C++ IPNetwork conversion demands more properties, this fixes
		// it; if the coroutine never resumes, up() still won't log.
		autoJs = autoJs.replace(
			'up: async () => {\n\t\t\tawait up(settings);\n\t\t},',
			'up: async () => {\n\t\t\tconsole.log("[AUTO] netExports.up() CALLED");\n\t\t\tawait up(settings);\n\t\t},\n\t\tdown, login, logout,'
		);
	} else {
		autoJs = autoJs.replace(
			'up: async () => {\n\t\t\tawait up(settings);\n\t\t},',
			'up: async () => {\n\t\t\tconsole.log("[AUTO] netExports.up() CALLED");\n\t\t\tawait up(settings);\n\t\t},'
		);
	}
	await page.route('**/tun/tailscale_tun.js', (r) =>
		r.fulfill({ status: 200, contentType: 'application/javascript', body: tunJs }));
	await page.route('**/tun/tailscale_tun_auto.js', (r) =>
		r.fulfill({ status: 200, contentType: 'application/javascript', body: autoJs }));
	if (process.env.TUN_TRACE_MODE === 'rich') {
		// Patch direct.js: observe the autoConf promise resolution/rejection
		// and TailscaleNetwork.prototype.up() calls.
		let directJs = fs.readFileSync(base + 'direct.js', 'utf8');
		const anchor = 'return Larg1.autoConf(Larg0.a1);';
		if (!directJs.includes(anchor)) {
			console.log('WARN: direct.js autoConf anchor not found');
		} else {
			directJs = directJs.replace(
				anchor,
				'(()=>{const p=Larg1.autoConf(Larg0.a1);p.then(v=>console.log("[DIRECT] autoConf RESOLVED "+(v&&v.constructor&&v.constructor.name)),e=>console.log("[DIRECT] autoConf REJECTED "+(e&&e.stack||e)));return p;})()'
			);
			const upAnchor = 'TailscaleNetwork.prototype.up=function(){';
			if (directJs.includes(upAnchor)) {
				directJs = directJs.replace(
					upAnchor,
					'TailscaleNetwork.prototype.up=function(){console.log("[DIRECT] TailscaleNetwork.prototype.up CALLED " + new Error().stack.split("\\n").slice(2,4).join(" "));'
				);
			} else {
				console.log('WARN: prototype.up anchor not found');
			}
			await page.route('**/tun/direct.js', (r) =>
				r.fulfill({ status: 200, contentType: 'application/javascript', body: directJs }));
			console.log('TUN_TRACE: direct.js interception installed');
		}
	}
	console.log('TUN_TRACE: module interception installed');
}
const ts = () => '+' + Math.round((Date.now() - t0) / 1000) + 's';
const events = [];
const ctrl = /\/key|ts2021|derp|machine|register|verify/;

// EVERY console line, in order, with timestamps — the FIRST error before the
// wedge is the target (webvm #222).
page.on('console', (m) => events.push(`${ts()} CONSOLE ${m.type()}: ${m.text()}`));
page.on('pageerror', (e) => events.push(`${ts()} PAGEERROR: ${e}`));
page.on('dialog', (d) => events.push(`${ts()} DIALOG: ${d.message()}`));

// Control-plane traffic: does /key happen at all? status? then /ts2021 WSS?
page.on('request', (r) => {
	if (ctrl.test(r.url())) events.push(`${ts()} REQ  ${r.method()} ${r.url()}`);
	else if (DEBUG_REQS) events.push(`${ts()} REQ  ${r.method()} ${r.url()}`);
});
page.on('response', (r) => {
	if (ctrl.test(r.url())) events.push(`${ts()} RES  ${r.status()} ${r.url()}`);
	else if (DEBUG_REQS) events.push(`${ts()} RES  ${r.status()} ${r.url()}`);
});
page.on('requestfailed', (r) => {
	if (ctrl.test(r.url()) || DEBUG_REQS)
		events.push(`${ts()} REQFAIL ${r.url()} ${r.failure()?.errorText || ''}`);
});
page.on('websocket', (ws) => {
	events.push(`${ts()} WS open ${ws.url()}`);
	ws.on('close', () => events.push(`${ts()} WS close ${ws.url()}`));
});

console.log('open', SESSION_URL.replace(KEY, 'KEY=' + KEY.slice(0, 12) + '…'));
await page.goto(SESSION_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });

// MANUAL_DRIVE=1: 20s after load, drive the tailnet directly from the page
// (the core never calls TailscaleNetwork.up()): import the tun module, call
// autoConf + up(). If the tailnet comes up, guest sockets should work.
if (process.env.MANUAL_DRIVE === '1') {
	await page.waitForTimeout(20000);
	await page.evaluate(() => {
		window.__manualDrive = (async () => {
			const net = await import('/cheerpx/tun/tailscale_tun_auto.js');
			console.log('[MANUAL] module loaded');
			const exports = await net.autoConf({
				loginUrlCb: (u) => console.log('[MANUAL] loginUrl', u),
				stateUpdateCb: (s) => console.log('[MANUAL] state', s),
				netmapUpdateCb: (m) => console.log('[MANUAL] netmap peers', m.peers.length),
				authKey: window.sessionStorage.getItem('authKey') || undefined,
				controlUrl: window.sessionStorage.getItem('controlUrl') || undefined,
				ipMap: {},
			});
			console.log('[MANUAL] autoConf done');
			// Wire the exports into the globals the CheerpX core's socket
			// emulation reads (the rS/direct.js path never sets them).
			window.cjTailscaleSocket = exports.tcpSocket;
			window.cjTailscaleUdpSocket = exports.udpSocket;
			window.cjTailscaleParseIp = exports.parseIP;
			window.cjTailscaleDumpIp = exports.dumpIP;
			window.cjEnableTailscale = true;
			console.log('[MANUAL] cjTailscale globals set');
			await exports.up();
			console.log('[MANUAL] up() returned');
		})().catch((e) => console.log('[MANUAL] FAILED: ' + (e && e.stack || e)));
	});
	events.push(`${ts()} MANUAL_DRIVE started`);
}
if (process.env.CLICK_CONNECT === '1') {
	await page.waitForTimeout(15000);
	const clicked = await page.evaluate(() => {
		const els = [...document.querySelectorAll('button, a, [role="button"], [tabindex]')];
		const hit = els.find((el) => /tailscale|connect/i.test((el.textContent || '')) || /tailscale|connect/i.test(el.id || ''));
		if (hit) { hit.click(); return hit.outerHTML.slice(0, 120); }
		return null;
	});
	events.push(`${ts()} CLICK_CONNECT: ${clicked || 'NO BUTTON FOUND'}`);
	console.log('clicked:', clicked || 'NO BUTTON FOUND');
	// try harder: any element with onclick/class hints
	if (!clicked) {
		const r = await page.evaluate(() => {
			const out = [];
			for (const el of document.querySelectorAll('button, a, [class*=tail], [class*=net], [class*=connect], [id*=tail], [id*=net]')) {
				out.push((el.tagName + '#' + el.id + '.' + el.className + ':' + (el.textContent || '').slice(0, 40)).slice(0, 100));
			}
			return out.slice(0, 20);
		});
		console.log('candidates:', JSON.stringify(r, null, 0));
	}
}

const seen = { lock: false, pageerror: null };
page.on('pageerror', (e) => {
	if (!seen.pageerror) { seen.pageerror = String(e); events.push(`${ts()} *** FIRST PAGEERROR *** ${e}`); }
});

const tStart = Date.now();
while (Date.now() - tStart < LIMIT_S * 1000) {
	const ok = await context.request
		.get(WEBDAV_BASE + 'webvm.lock', { headers: AUTH_HEADER })
		.then((r) => r.ok())
		.catch(() => false);
	if (ok) {
		seen.lock = true;
		events.push(`${ts()} webvm.lock appeared on the WebDAV backend`);
		break;
	}
	await page.waitForTimeout(5000);
}

console.log('\n==== RESULT: lock=' + seen.lock + ' after ' + Math.round((Date.now() - tStart) / 1000) + 's ====\n');

// Order events; show everything that happened in the first 30s (crash window)
// plus all errors, then the rest.
const crashWindow = events.filter((e) => /^(?:\+([0-9]+)s )/.test(e) && Number(e.match(/^\+([0-9]+)s/)[1]) <= 30);
console.log('--- events (first 30s) ---');
for (const e of crashWindow) console.log('  ' + e);

console.log('--- errors (all time) ---');
for (const e of events.filter((l) => /PAGEERROR|Unexpected exit|signature mismatch|CORS|unsupported client|Error|error|failed|REQFAIL|Refused|violates/.test(l)))
	console.log('  ' + e);

console.log('--- control-plane sequence ---');
for (const e of events.filter((l) => / (REQ|RES|WS) /.test(l)))
	console.log('  ' + e);

console.log('--- FULL event log ---');
for (const e of events) console.log('  ' + e);

// Network resources actually fetched (from the browser's own resource timing
// — includes fetches that bypassed our request listeners).
const resources = await page.evaluate(() =>
	performance.getEntriesByType('resource')
		.map((r) => r.name)
		.filter((n) => /tailscale|ipstack|direct|cxcore|cx_esm|blob/.test(n))
);
console.log('--- resource timing tail ---');
for (const n of resources.slice(-15)) console.log('  ' + n);

// Dump captured blob contents to files for analysis.
const blobs = await page.evaluate(() => window.__blobs || {});
const fs2 = await import('node:fs');
let i = 0;
for (const [url, text] of Object.entries(blobs)) {
	const f = `/tmp/webvm-blob-${i}-${url.slice(5, 15).replace(/\W/g, '')}.txt`;
	fs2.writeFileSync(f, text);
	console.log('blob written: ' + f + ' (' + text.length + ' chars)');
	i++;
}

await browser.close();
process.exit(seen.lock ? 0 : 1);
