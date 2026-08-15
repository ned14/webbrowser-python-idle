import { writable } from 'svelte/store';
import { browser } from '$app/environment'

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
let dashboardUrl = controlUrl ? null : "https://login.tailscale.com/admin/machines";
let resolveLogin = null;
let rejectLogin = null;
let loginPromise = null;
let connectionState = writable("DISCONNECTED");
let exitNode = writable(false);

function resetLoginPromise()
{
	loginPromise = new Promise((f,r) => {
		resolveLogin = f;
		rejectLogin = r;
	});
}

function validateLoginUrl(url)
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
			connectionState.set("CONNECTED");
			break;
		}
	}
}

function netmapUpdateCb(map)
{
	networkData.currentIp = map.self.addresses[0];
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
	connectionState.set("LOGINSTARTING");
	const url = await loginPromise;
	networkData.loginUrl = url;
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
		case "CONNECTED":
			return {
				buttonText: `IP: ${networkData.currentIp}`,
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

async function startTailnet()
{
	try
	{
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
	}
	catch(e)
	{
		console.warn('tailnet driver failed:', e);
		tunExports = null;
	}
}

if (browser && controlUrl)
{
	startTailnet();
}

// The networkInterface also carries the socket adapter the CheerpX core's
// guest-socket dispatcher calls (legacy-path contract: a47.TCPSocket etc.):
// the guest's connect(2) syscalls are handed to these, so they MUST be
// backed by the tun exports for any guest traffic to flow.
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
			// The `closed` promise MUST resolve when the socket goes away:
			// the core awaits it during guest process teardown, and a
			// never-resolving promise wedges the core's socket handling
			// (the guest process that used the socket never finishes
			// exiting, blocking later guest processes). It resolves on
			// EVERY failure path too — a failed guest connect is the normal
			// state when the data path is down.
			let resolveClosed;
			const closed = new Promise((res) => { resolveClosed = res; });
			const sock = new tunExports.tcpSocket();
			const ip = tunExports.parseIP(remoteIP);
			const opened = new Promise((resolve, reject) => {
				const fail = (err) => {
					try { sock.close(); } catch (e) {}
					resolveClosed();
					reject(err);
				};
				if (sock.bind(0) !== 0) { fail(new Error('bind failed')); return; }
				const rc = sock.connect(ip, remotePort);
				if (rc !== 0) { fail(new Error('connect failed rc=' + rc)); return; }
				sock.waitOutgoing().then(() => {
					const readable = new ReadableStream({
						type: 'bytes',
						autoAllocateChunkSize: 1500,
						async pull(controller) {
							for (;;) {
								const view = controller.byobRequest ? controller.byobRequest.view : new Uint8Array(1500);
								const n = sock.recv(view, 0, view.length);
								if (n > 0) {
									if (controller.byobRequest) controller.byobRequest.respond(n);
									else controller.enqueue(view.slice(0, n));
									return;
								}
								if (n === -11) { await sock.waitIncoming(); continue; } // EAGAIN
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
						},
						close() { try { sock.shutdownTx(); } catch (e) {} },
						abort() { try { sock.close(); } catch (e) {} },
					});
					resolve({
						readable: readable,
						writable: writable,
						remoteAddress: remoteIP,
						localAddress: '0.0.0.0',
						remotePort: remotePort,
						localPort: 0,
					});
				}, (e) => { try { sock.close(); } catch (x) {} resolveClosed(); reject(e); });
			});
			return {
				opened: opened,
				closed: closed,
				close: () => { try { sock.close(); } catch (e) {} resolveClosed(); return Promise.resolve(); },
			};
		}
		catch (e)
		{
			console.warn('tailnet TCPSocket failed:', e);
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
						for (;;) {
							const buf = new Uint8Array(1500);
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
							if (n === -11) { await sock.waitIncoming(); continue; } // EAGAIN
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
