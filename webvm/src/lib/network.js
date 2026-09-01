import { writable } from 'svelte/store';
import { browser } from '$app/environment'

// The connection-state names — single source of truth. NetworkingTab.svelte
// and updateButtonData switch on these literals; never retype them.
export const NETWORK_STATES = Object.freeze({
	DISCONNECTED: "DISCONNECTED",
	DOWNLOADING: "DOWNLOADING",
	LOGINSTARTING: "LOGINSTARTING",
	LOGINREADY: "LOGINREADY",
	LOGINFAILED: "LOGINFAILED",
	UNREACHABLE: "UNREACHABLE",
	CONNECTED: "CONNECTED",
	IPCOPIED: "IPCOPIED",
});

let authKey = undefined;
let controlUrl = undefined;
if(browser)
{
	// The hash -> sessionStorage move happens in an inline script at the top
	// of app.html, before this module (and any component code) is evaluated.
	controlUrl = window.sessionStorage.getItem("controlUrl") || undefined;
	// NEVER use #authKey without a matching #controlUrl: WebVM would then
	// auto-register with Tailscale's public control server.
	authKey = controlUrl ? (window.sessionStorage.getItem("authKey") || undefined) : undefined;
}
// The self-hosted control plane's admin page. NEVER the public
// login.tailscale.com: this stack is LAN-only by design (no public Tailscale
// anywhere), so when a controlUrl exists the CONNECTED button links to THIS
// deployment's headscale web UI; without one there is no tailnet admin at
// all. (The public-URL inversion this replaces would have shipped users to
// Tailscale's login page the moment a refactor surfaced it.)
let dashboardUrl = controlUrl ? new URL("/web", controlUrl).href : null;
let resolveLogin = null;
let rejectLogin = null;
let loginPromise = null;
// The single writable store; components reach it via networkData.connectionState.
let connectionState = writable("DISCONNECTED");
let exitNode = writable(false);

// Capability flag: does THIS DEPLOYMENT have any tailnet support at all
// (authKey/controlUrl rendered)? browser/none builds do not — their
// Networking entry is fully INERT (no panel even), unlike the
// reachable-gateway-down case which stays clickable for Retry.
export const networkingEnabled = !!(browser && controlUrl);

// networkReachable covers BOTH "deployment has no tailnet capability"
// (browser/none builds: no authKey/controlUrl rendered) AND "capability
// present but the control plane is not" — e.g. `make up` (server only, no
// gateway container): the baked controlUrl points at a dead endpoint, and
// without a gate the tailscale client would auto-start and hammer it with
// WebSocket retries forever. The sidebar icon subscribes to this store, so
// either case gets the SAME crossed-out disabled treatment.
export const networkReachable = writable(false);

const CONTROL_HEALTH_TIMEOUT_MS = 3000;

async function controlPlaneReachable()
{
	if(!controlUrl)
		return false;
	try
	{
		const controller = new AbortController();
		const timer = setTimeout(() => controller.abort(), CONTROL_HEALTH_TIMEOUT_MS);
		await fetch(new URL("/health", controlUrl).href,
			{ mode: "no-cors", cache: "no-store", signal: controller.signal });
		clearTimeout(timer);
		// Opaque no-cors response: reaching ANYTHING at the origin counts.
		return true;
	}
	catch(e)
	{
		return false;
	}
}

// Probe the control plane, and only spawn the tailscale client when it is
// actually reachable (this is what keeps `make up`-without-gateway sessions
// free of WebSocket retry spam). Returns whether networking started.
export async function tryStartNetworking()
{
	if(!networkingEnabled || !(await controlPlaneReachable()))
	{
		connectionState.set("UNREACHABLE");
		networkReachable.set(false);
		return false;
	}
	// A retry re-enables real control sockets and clears the rejection
	// streak (the watchdog re-trips on its own if the key is still bad).
	connectedNow = false;
	handshakeFailures = 0;
	suppressControlSockets = false;
	connectionState.set("DISCONNECTED");
	networkReachable.set(true);
	startTailnet();
	return true;
}

function resetLoginPromise()
{
	loginPromise = new Promise((f,r) => {
		resolveLogin = f;
		rejectLogin = r;
	});
}

// Exported for the vitest suite: the login-URL gate the wasm client's
// loginUrlCb applies. https/http only — the client navigates the login
// popup to this URL, so anything else (javascript:, file:, a relative URL)
// must never reach window.open.
export function validateLoginUrl(url)
{
	const parsedUrl = new URL(url);
	if(parsedUrl.protocol != "https:" && parsedUrl.protocol != "http:")
		throw new Error("Invalid Tailscale login URL scheme");
	return parsedUrl.href;
}

function loginUrlCb(url)
{
	try
	{
		url = validateLoginUrl(url);
	}
	catch(e)
	{
		connectionState.set("LOGINFAILED");
		rejectLogin(e);
		resetLoginPromise();
		return;
	}
	connectionState.set("LOGINREADY");
	resolveLogin(url);
}

function stateUpdateCb(state)
{
	switch(state)
	{
		case 6 /*Running*/:
		{
			connectedNow = true;
			handshakeFailures = 0;
			suppressControlSockets = false;
			connectionState.set("CONNECTED");
			break;
		}
	}
}

// --- Control-plane rejection watchdog -------------------------------------
// headscale answering /health does NOT mean registration succeeds: a stale/
// rejected authKey makes the client loop wss://…/ts2021 open→immediate-close
// forever (~25 attempts/min of console spam) while the sidebar still shows
// networking as available. Both the driver and the cheerpOSNetInit heal
// create their control sockets through window.WebSocket, so counting here
// covers every client instance. After 5 consecutive rejected handshakes
// (never reaching Running) the session flips to UNREACHABLE — sidebar cross-
// out, panel explanation + Retry — and further /ts2021 constructions get a
// silent stand-in socket: the wasm client keeps its internal retry cadence
// but emits zero network traffic. A later Running (or the panel's Retry)
// clears the suppression.
var connectedNow = false;
var handshakeFailures = 0;
var suppressControlSockets = false;
var HANDSHAKE_FAILURE_LIMIT = 5;

// The pure close-event decision (extracted for tests): a session that
// reached Running ended normally and resets the streak; anything else is
// one more rejected handshake that trips the UNREACHABLE gate at the limit.
export function applyControlSocketClose(connectedNow, handshakeFailures, limit = HANDSHAKE_FAILURE_LIMIT) {
	if (connectedNow)
		return { handshakeFailures: 0, shouldTrip: false };
	const next = handshakeFailures + 1;
	return { handshakeFailures: next, shouldTrip: next >= limit };
}

function tripUnreachable()
{
	if (connectedNow)
		return;
	console.warn("tailnet: control plane rejecting handshakes; disabling networking for this session (Retry in the Networking panel re-probes)");
	handshakeFailures = 0;
	suppressControlSockets = true;
	connectionState.set("UNREACHABLE");
	networkReachable.set(false);
}

if (browser && networkingEnabled)
{
	const RealWebSocket = window.WebSocket;
	function PatchedWebSocket(url, protocols)
	{
		const isControl = typeof url === "string" && url.includes("/ts2021");
		if (isControl && suppressControlSockets)
		{
			// Silent stand-in: never opens, closes cleanly, no traffic.
			const dummy = new EventTarget();
			dummy.close = function() {};
			dummy.send = function() { throw new Error("networking unavailable"); };
			setTimeout(() => dummy.dispatchEvent(new CloseEvent("close")), 0);
			return dummy;
		}
		const ws = protocols === undefined ? new RealWebSocket(url)
			: new RealWebSocket(url, protocols);
		if (isControl)
		{
			ws.addEventListener("open", () => { connectedNow = false; });
			ws.addEventListener("close", () => {
				const verdict = applyControlSocketClose(connectedNow, handshakeFailures);
				handshakeFailures = verdict.handshakeFailures;
				if (verdict.shouldTrip)
					tripUnreachable();
			});
		}
		return ws;
	}
	PatchedWebSocket.prototype = RealWebSocket.prototype;
	PatchedWebSocket.CONNECTING = RealWebSocket.CONNECTING;
	PatchedWebSocket.OPEN = RealWebSocket.OPEN;
	PatchedWebSocket.CLOSING = RealWebSocket.CLOSING;
	PatchedWebSocket.CLOSED = RealWebSocket.CLOSED;
	window.WebSocket = PatchedWebSocket;
}

function netmapUpdateCb(map)
{
	// A pre-update netmap can carry an empty/absent address list; the
	// CONNECTED button must never show "IP: undefined".
	networkData.currentIp = (map.self && map.self.addresses && map.self.addresses.length)
		? map.self.addresses[0]
		: null;
	var exitNodeFound = false;
	for(var i=0; i < map.peers.length;i++)
	{
		if(map.peers[i].exitNode)
		{
			exitNodeFound = true;
			break;
		}
	}
	if(exitNodeFound)
	{
		exitNode.set(true);
	}
}

export async function startLogin()
{
	// Ordering (fixed 2026-08-29): the wasm client can invoke loginUrlCb
	// SYNCHRONOUSLY during cx.networkLogin() — i.e. BEFORE startLogin runs —
	// so setting LOGINSTARTING here would overwrite the LOGINREADY state
	// already set by loginUrlCb and the sidebar button would stick at
	// "Starting Login…" until the client reaches Running (the LOGINREADY
	// clickable state was dead). Set LOGINREADY after the URL is known; the
	// popup flow (WebVM.svelte handleConnect) consumes the returned URL and
	// the button shows the clickable login link until Running arrives.
	const url = await loginPromise;
	networkData.loginUrl = url;
	connectionState.set("LOGINREADY");
	return url;
}

async function handleCopyIP(event)
{
	// To prevent the default contexmenu from showing up when right-clicking..
	event.preventDefault();
	// Copy the IP to the clipboard.
	try
	{
		await window.navigator.clipboard.writeText(networkData.currentIp)
		connectionState.set("IPCOPIED");
		setTimeout(() => {
			connectionState.set("CONNECTED");
		}, 2000);
	}
	catch(msg)
	{
		console.log("Copy ip to clipboard: Error: " + msg);
	}
}

export function updateButtonData(state, handleConnect) {
	switch(state) {
		case "DISCONNECTED":
			return {
				buttonText: "Connect to Tailscale",
				isClickable: true,
				clickHandler: handleConnect,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
		case "DOWNLOADING":
			return {
				buttonText: "Loading IP stack...",
				isClickable: false,
				clickHandler: null,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
		case "LOGINSTARTING":
			return {
				buttonText: "Starting Login...",
				isClickable: false,
				clickHandler: null,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
		case "LOGINREADY":
			return {
				buttonText: "Login to Tailscale",
				isClickable: true,
				clickHandler: null,
				clickUrl: networkData.loginUrl,
				buttonTooltip: null,
				rightClickHandler: null
			};
		case "LOGINFAILED":
			return {
				buttonText: "Invalid login URL",
				isClickable: false,
				clickHandler: null,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
		case "UNREACHABLE":
			// Control plane did not answer /health (gateway down in a
			// server-only launch). The sidebar icon is crossed out while
			// this state holds; the button is the retry affordance.
			return {
				buttonText: "Control plane unreachable — click to retry",
				isClickable: true,
				clickHandler: handleConnect,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
		case "CONNECTED":
			return {
				buttonText: `IP: ${networkData.currentIp || "…"}`,
				isClickable: true,
				clickHandler: null,
				clickUrl: networkData.dashboardUrl,
				buttonTooltip: "Right-click to copy",
				rightClickHandler: handleCopyIP
			};
		case "IPCOPIED":
			return {
				buttonText: "Copied!",
				isClickable: false,
				clickHandler: null,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
		default:
			return {
				buttonText: `Text for state: ${state}`,
				isClickable: false,
				clickHandler: null,
				clickUrl: null,
				buttonTooltip: null,
				rightClickHandler: null
			};
	}
}

// App-side tailnet driver (workaround for the CheerpX core defect — verified
// 2026-08-15: with runtime 1.3.7/1.3.8 the core's own network-init flow stops
// after autoConf() resolves and never calls netExports.up(), so tailscale.wasm
// is never fetched and the tailnet never starts — on this stack AND on the
// reference webvm.io in current Chromium). Driving the tun module directly
// starts the client: tailscale.wasm is fetched, the control plane is reached,
// and the client reaches state 6 (Running). The guest's data path remains
// blocked by a second core defect (the guest NIC is never created and the
// guest's first socket attempt crashes the runtime) — see
// plans/networking-bug.md §15.
let tunExports = null;

// --- Tailnet stuck-state watchdog ------------------------------------------
// The pinned wasm client's netmap validation is a RACE: after registration
// it can stick in "authReconfig: netmap not yet valid. Skipping." forever
// (a partial netmap push — the tailnet never comes up, the guest sync
// agent waits in vain; observed repeatedly 2026-08-30, including with a
// healthy control plane + DERP). A fresh driver start rolls the dice again
// (new machine key, new registration, new netmap push), so a stuck session
// self-heals: if the client has not reached Running within STUCK_MS of a
// start, the driver is restarted — bounded, so a genuinely dead deployment
// does not churn forever. Re-running autoConf+up is the same operation the
// core's own net-init heal performs, so it is known-safe under the runtime.
var tailnetAttempts = 0;
const TAILNET_MAX_ATTEMPTS = 5;
// 45s per attempt: the client's registration takes ~2s, so a stuck attempt
// is detectable well before the guest sync agent's tailnet-wait window
// (200s) expires — with attempts at t=0/45/90/135/180s a winning roll still
// syncs inside the agent's window and the E2E's 240s lock poll.
const TAILNET_STUCK_MS = 45000;
function scheduleTailnetWatchdog()
{
	tailnetAttempts++;
	setTimeout(() => {
		if (connectedNow)
			return; // Running reached — healthy
		if (tailnetAttempts >= TAILNET_MAX_ATTEMPTS)
			return;
		console.warn("tailnet: client did not reach Running; restarting the driver (attempt " + (tailnetAttempts + 1) + ")");
		startTailnet();
	}, TAILNET_STUCK_MS);
}

async function startTailnet()
{
	try
	{
		// ROOT-ABSOLUTE on purpose (NOT siteBase or a relative path —
		// regression 2026-09-01, CI network/sync specs): the tun glue module
		// must be imported under EXACTLY the URL the CheerpX core's own
		// net-init (cheerpOSNetInit, run by the heal below) imports, or the
		// browser ends up with TWO module instances of tailscale_tun.js —
		// each constructing its own singleton IpStack. The IpStack is a
		// module-level singleton; two instances mean the SECOND client's
		// IpStack.up({localIp...}) reconfigures a stack the first client's
		// sockets still use, and the guest's first connect() hits a
		// half-torn-down stack (EINVAL -> ipstack wasm "table index is out
		// of bounds" -> the whole session dies before the sync agent's
		// lease lands; verified 2026-09-01: siteBase-prefixed URL fails
		// 3/3, root-absolute passes). The core resolves its own tun URL
		// root-absolutely, so this must match. The tailnet never runs on
		// GitHub Pages (no authKey/controlUrl rendered there), so the
		// siteBase indirection is not needed for the pages deployment.
		const net = await import('/cheerpx/tun/tailscale_tun_auto.js');
		tunExports = await net.autoConf({
			loginUrlCb: loginUrlCb,
			stateUpdateCb: stateUpdateCb,
			netmapUpdateCb: netmapUpdateCb,
			authKey: authKey,
			controlUrl: controlUrl,
			ipMap: {},
		});
		// Belt-and-braces for the core's socket globals (the legacy
		// cheerpOSNetInit path sets these).
		window.cjTailscaleSocket = tunExports.tcpSocket;
		window.cjTailscaleUdpSocket = tunExports.udpSocket;
		window.cjTailscaleParseIp = tunExports.parseIP;
		window.cjTailscaleDumpIp = tunExports.dumpIP;
		window.cjEnableTailscale = true;
		await tunExports.up();
		// CRITICAL (verified 2026-08-16, plans/networking-bug.md §16.8): with
		// ONLY the driver's autoConf+up, the guest's data path stays broken —
		// the guest's connect(2) never completes app-side even though the
		// netstack finishes the TCP handshake (nc -z 100.64.0.1 8082 hangs;
		// the sync agent retries forever). Re-running the CORE's own net-init
		// (cheerpOSNetInit — the stock flow the core arms itself with) right
		// after the driver is up heals it: the second autoConf+up on the tun
		// module re-establishes the working guest data path, and the sync
		// lease/snapshot then land on the backend within ~2 min (2/2 runs
		// with the call, 0/5 without). The core's own invocation (if it ever
		// runs) is idempotent with this one.
		//
		// cheerpOSNetInit only becomes a global when the runtime injects and
		// evaluates its cheerpOS.js via a dynamically created <script> tag —
		// an async step that races this driver reaching `up()`. A single
		// instanceof check here could therefore MISS the healer entirely and
		// leave the guest data path down, so poll for the global (up to ~20s)
		// before invoking it once; if it still has not appeared (slow CI/WASM
		// load), a background watcher keeps looking for a few minutes so a
		// late injection still heals the session instead of failing silently.
		const runCoreNetInitHeal = () => {
			// ROOT-ABSOLUTE, matching the driver's import above: the core
			// resolves this URL itself, and a mismatch yields TWO tun-glue
			// module instances / TWO IpStack singletons (the 2026-09-01
			// regression — see startTailnet's import comment).
			window.cheerpOSNetInit(
				'/cheerpx/tun/tailscale_tun_auto.js',
				loginUrlCb,
				authKey,
				controlUrl,
				null, // dnsIp
				{}, // ipMap
				netmapUpdateCb,
				() => {}
			);
		};
		let healed = false;
		for (let i = 0; i < 80; i++)
		{
			if (typeof window.cheerpOSNetInit === 'function')
			{
				runCoreNetInitHeal();
				healed = true;
				break;
			}
			await new Promise((r) => setTimeout(r, 250));
		}
		if (!healed)
		{
			let tries = 0;
			const watcher = setInterval(() => {
				tries += 1;
				if (typeof window.cheerpOSNetInit === 'function')
				{
					clearInterval(watcher);
					runCoreNetInitHeal();
				}
				else if (tries >= 60) // ~5 min total watch window
				{
					clearInterval(watcher);
					console.warn('tailnet driver: cheerpOSNetInit never appeared; guest data path may stay down');
				}
			}, 5000);
		}
		// The stuck-state watchdog starts once the driver is up (a fresh
		// start rolls the netmap-validity dice again if Running never
		// arrives).
		scheduleTailnetWatchdog();
	}
	catch(e)
	{
		console.warn('tailnet driver failed:', e);
		tunExports = null;
	}
}

if (browser && controlUrl)
{
	// Gated auto-start: probe /health first so a missing/dead gateway never
	// spawns the client (no WebSocket retry spam); tryStartNetworking sets
	// the UNREACHABLE state + store for the sidebar icon.
	tryStartNetworking();
}

// Build the { opened, closed, close } socket shape for a CONNECTED tun
// tcpSocket (outbound after waitOutgoing, or one returned by the accept
// loop). The `closed` promise MUST resolve when the socket goes away: the
// core awaits it during guest process teardown, and a never-resolving
// promise wedges the core's socket handling (the guest process that used
// the socket never finishes exiting, blocking later guest processes). It
// resolves on EVERY failure path too — a failed guest connect is the normal
// state when the data path is down.
function connectedTcpSocket(sock, remoteAddress, remotePort, localPort)
{
	let resolveClosed;
	const closed = new Promise((res) => { resolveClosed = res; });
	const opened = new Promise((resolve, reject) => {
		const readable = new ReadableStream({
			type: 'bytes',
			autoAllocateChunkSize: 1500,
			async pull(controller) {
				for (;;) {
					let view;
					let n;
					try {
						view = controller.byobRequest ? controller.byobRequest.view : new Uint8Array(1500);
						n = sock.recv(view, 0, view.length);
					} catch (e) {
						// A throwing recv (tun torn down mid-read) must close
						// the socket AND resolve `closed` — the invariant
						// below — or the core wedges on guest teardown.
						try { sock.close(); } catch (x) {}
						resolveClosed();
						controller.error(e);
						return;
					}
					if (n > 0) {
						if (controller.byobRequest) controller.byobRequest.respond(n);
						else controller.enqueue(view.slice(0, n));
						return;
					}
					if (n === -11) {
						try { await sock.waitIncoming(); continue; } // EAGAIN
						catch (e) {
							// Same invariant on a rejecting waitIncoming.
							try { sock.close(); } catch (x) {}
							resolveClosed();
							controller.error(e);
							return;
						}
					}
					if (controller.byobRequest) controller.byobRequest.respond(0);
					controller.close();
					try { resolveClosed(); } catch (e) {}
					return;
				}
			},
			cancel() { try { sock.close(); } catch (e) {} resolveClosed(); },
			close: () => { try { sock.close(); } catch (e) {} resolveClosed(); },
		});
		const writable = new WritableStream({
			async write(chunk) {
				// send(array, offset, len) — the IpStack signature
				// (the runtime's own TCPWrapper write loop).
				const data = chunk instanceof Uint8Array ? chunk : new Uint8Array(chunk);
				let off = 0;
				try {
					while (off < data.length) {
						const n = sock.send(data, off, data.length - off);
						if (n > 0) { off += n; continue; }
						if (n === -11) {
							// EAGAIN — tx buffer full. MUST yield to
							// the event loop: the tun drains the
							// buffer asynchronously, so a synchronous
							// retry loop never makes progress (the
							// socket's send buffer stays full forever
							// and the write never completes).
							await new Promise((r) => setTimeout(r, 5));
							continue;
						}
						throw new Error('send failed rc=' + n);
					}
				} catch (e) {
					// A throwing/rejecting send must also resolve `closed`
					// (the invariant) before the write failure propagates.
					try { sock.close(); } catch (x) {}
					resolveClosed();
					throw e;
				}
			},
			close() { try { sock.shutdownTx(); } catch (e) {} },
			abort() { try { sock.close(); } catch (e) {} resolveClosed(); },
		});
		resolve({
			readable: readable,
			writable: writable,
			remoteAddress: remoteAddress,
			localAddress: '0.0.0.0',
			remotePort: remotePort,
			localPort: localPort,
		});
	});
	return {
		opened: opened,
		closed: closed,
		close: () => { try { sock.close(); } catch (e) {} resolveClosed(); return Promise.resolve(); },
	};
}

// The networkInterface also carries the socket adapter the CheerpX core's
// guest-socket dispatcher calls (legacy-path contract: a47.TCPSocket etc.):
// the guest's connect(2) syscalls are handed to these, so they MUST be
// backed by the tun exports for any guest traffic to flow. Outbound
// connects go to TCPSocket; guest bind(2)/listen(2) (busybox nc binds
// before connecting; servers bind+listen) go to TCPServerSocket — both are
// required or the core's dispatcher crashes on the missing method.
export const networkInterface = {
	authKey: authKey,
	controlUrl: controlUrl,
	loginUrlCb: loginUrlCb,
	stateUpdateCb: stateUpdateCb,
	up: async () => { if (tunExports) await tunExports.up(); },
	// The core's socket dispatcher expects a socket shaped like the runtime's
	// own TCPWrapper result: { opened, closed, close } where opened resolves
	// with { readable, writable, remoteAddress, localAddress, remotePort,
	// localPort } (mirrors direct.js's TailscaleNetwork.TCPSocket +
	// setupClient: parseIP -> new tcpSocket() -> bind(0) -> connect ->
	// waitOutgoing() -> ReadableStream/WritableStream over recv/send).
	TCPSocket: (remoteIP, remotePort) => {
		if (!tunExports) return null;
		try {
			const sock = new tunExports.tcpSocket();
			const ip = tunExports.parseIP(remoteIP);
			const wrapper = connectedTcpSocket(sock, remoteIP, remotePort, 0);
			const opened = new Promise((resolve, reject) => {
				if (sock.bind(0) !== 0) { wrapper.close(); reject(new Error('bind failed')); return; }
				const rc = sock.connect(ip, remotePort);
				if (rc !== 0) { wrapper.close(); reject(new Error('connect failed rc=' + rc)); return; }
				sock.waitOutgoing().then(() => {
					resolve(wrapper.opened);
				}, (e) => { wrapper.close(); reject(e); });
			});
			// The core awaits the SAME opened promise the wrapper resolved
			// with (streams + addresses); closed/close stay on the wrapper.
			return {
				opened: opened,
				closed: wrapper.closed,
				close: wrapper.close,
			};
		}
		catch (e)
		{
			console.warn('tailnet TCPSocket failed:', e);
			return null;
		}
	},
	// Guest bind(2)/listen(2) on a TCP socket: bind the tun socket, listen,
	// and stream accepted connections out of a ReadableStream as
	// { opened, closed, close } wrappers (mirrors direct.js's
	// TailscaleNetwork.TCPServerSocket + TCPWrapper.listen/accept:
	// bind(localPort) -> listen() -> accept()/waitIncoming() loop).
	TCPServerSocket: (addr, opts) => {
		if (!tunExports) return null;
		try {
			const localPort = (opts && opts.localPort) | 0;
			let resolveClosed;
			const closed = new Promise((res) => { resolveClosed = res; });
			const sock = new tunExports.tcpSocket();
			const opened = new Promise((resolve, reject) => {
				const fail = (err) => {
					try { sock.close(); } catch (e) {}
					resolveClosed();
					reject(err);
				};
				if (sock.bind(localPort) !== 0) { fail(new Error('tcp bind failed')); return; }
				sock.listen();
				const readable = new ReadableStream({
					async pull(controller) {
						for (;;) {
							let acc;
							try { acc = sock.accept(); }
							catch (e) {
								controller.error(e);
								try { sock.close(); } catch (x) {}
								resolveClosed();
								return;
							}
							if (acc) {
								const remoteAddress = tunExports.dumpIP(acc.addr);
								// Accepted sockets are already connected:
								// hand them out as ready-made wrappers.
								controller.enqueue(connectedTcpSocket(acc.socket, remoteAddress, acc.port, localPort));
								return;
							}
							// EAGAIN — a connect is pending but not yet
							// established. NOTE: `sock.waitIncoming()` must
							// NOT be awaited here — the IpStack's
							// waitIncoming busy-spins the browser's main
							// thread when no connection ever arrives (the
							// rebuilt tailscale.wasm consumes inbound TCP for
							// the node's own IP, so the tun never delivers —
							// plans/networking-bug.md §16.9; observed 2026-08-18
							// as a hard page freeze on ANY bind+listen). Yield
							// to the event loop and re-poll accept() instead.
							// 250 ms keeps the emulated-vCPU cost of a
							// long-lived listening socket (~4 crossings/s)
							// well below the old 100 ms cadence while staying
							// far under human-perceptible accept latency.
							await new Promise((res) => setTimeout(res, 250));
						}
					},
					cancel() { try { sock.close(); } catch (e) {} resolveClosed(); },
					close: () => { try { sock.close(); } catch (e) {} resolveClosed(); },
				});
				resolve({ readable: readable, localAddress: addr, localPort: localPort });
			});
			return {
				opened: opened,
				closed: closed,
				close: () => { try { sock.close(); } catch (e) {} resolveClosed(); return Promise.resolve(); },
			};
		}
		catch (e)
		{
			console.warn('tailnet TCPServerSocket failed:', e);
			return null;
		}
	},
	// UDP: same { opened, closed, close } shape; opened resolves with
	// { readable, writable, localAddress, localPort } where readable carries
	// UDPMessage objects { data, remoteAddress, remotePort } and the writable
	// accepts them (mirrors direct.js's UDPWrapper.bind).
	UDPSocket: (opts) => {
		if (!tunExports) return null;
		try {
			let resolveClosed;
			const closed = new Promise((res) => { resolveClosed = res; });
			const sock = new tunExports.udpSocket();
			const opened = new Promise((resolve, reject) => {
				const fail = (err) => {
					try { sock.close(); } catch (e) {}
					resolveClosed();
					reject(err);
				};
				const port = (opts && opts.localPort) | 0;
				if (sock.bind(port) !== 0) { fail(new Error('udp bind failed')); return; }
				const readable = new ReadableStream({
					async pull(controller) {
						// ONE reusable receive buffer per socket: recv fills
						// it and the enqueued UDPMessage takes a slice COPY
						// (data: buf.slice(0, n)), so the buffer can be
						// refilled by the next datagram instead of allocating
						// 1500 bytes per packet (DNS/tailscale chatter makes
						// this frequent).
						const buf = new Uint8Array(1500);
						for (;;) {
							const addr = { addr: 0, port: 0 };
							const n = sock.recv(buf, 0, buf.length, addr);
							if (n > 0) {
								controller.enqueue({
									data: buf.slice(0, n),
									remoteAddress: tunExports.dumpIP(addr.addr),
									remotePort: addr.port,
								});
								return;
							}
				if (n === -11) {
					try { await sock.waitIncoming(); continue; } // EAGAIN
					catch (e) {
						// Tun torn down underneath us: error the stream AND
						// resolve `closed` — a stream error does not invoke
						// the source's cancel(), so without this the promise
						// never settles and the core wedges on teardown.
						try { sock.close(); } catch (x) {}
						controller.error(e);
						resolveClosed();
						return;
					}
				}
							controller.close();
							return;
						}
					},
					cancel() { try { sock.close(); } catch (e) {} },
				});
				const writable = new WritableStream({
					write(msg) {
						const d = msg.data instanceof Uint8Array ? msg.data : new Uint8Array(msg.data);
						const ip = tunExports.parseIP(msg.remoteAddress);
						const rc = sock.sendto(d, ip, msg.remotePort);
						if (rc < 0 && rc !== -11) throw new Error('udp send failed rc=' + rc);
					},
					close() {},
					abort() { try { sock.close(); } catch (e) {} },
				});
				resolve({ readable, writable, localAddress: '0.0.0.0', localPort: port });
			});
			return {
				opened: opened,
				closed: closed,
				close: () => { try { sock.close(); } catch (e) {} resolveClosed(); return Promise.resolve(); },
			};
		}
		catch (e)
		{
			console.warn('tailnet UDPSocket failed:', e);
			return null;
		}
	},
	parseIP: (s) => tunExports ? tunExports.parseIP(s) : null,
	dumpIP: (s) => tunExports ? tunExports.dumpIP(s) : null,
};

// Expose the socket adapter for the E2E probes (data-path-probe/stream-put
// probe drive the SAME wrapper the core uses, instead of a copied
// implementation that can silently drift).
if (typeof window !== 'undefined') {
	window.cjTailscaleAdapter = networkInterface;
}

export const networkData = { currentIp: null, connectionState: connectionState, exitNode: exitNode, loginUrl: null, dashboardUrl: dashboardUrl }

resetLoginPromise();
