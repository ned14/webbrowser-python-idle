<script>
	import { onMount, tick } from 'svelte';
	import { get } from 'svelte/store';
	import SideBar from '$lib/SideBar.svelte';
	import '$lib/global.css';
	import '@xterm/xterm/css/xterm.css'
	import '@fortawesome/fontawesome-free/css/all.min.css'
	import { networkInterface, startLogin } from '$lib/network.js'
	import { cpuActivity, diskActivity, cpuPercentage, diskLatency } from '$lib/activities.js'
	import { introMessage } from '$lib/messages.js'

	export let configObj = null;
	export let processCallback = null;
	export let cacheId = null;
	export let cpuActivityEvents = [];
	export let diskLatencies = [];
	export let activityEventsInterval = 0;

	var term = null;
	var cx = null;
	var fitAddon = null;
	var cxReadFunc = null;
	var blockCache = null;
	var processCount = 0;
	var curVT = 0;
	var sideBarPinned = false;
	// Fatal VM failure shown as a full-screen overlay with the exact reason:
	// phase "boot" = the guest never started, "runtime" = it was running and
	// stopped. `bootedOnce` flips after the first cx.run() completes.
	let fatal = null;
	let bootedOnce = false;
	function showFatal(phase, err)
	{
		var message = err && err.message ? err.message : String(err);
		var detail = err && err.stack ? err.stack : "";
		console.error("[WebVM] " + phase + " failed:", err);
		fatal = { phase: phase, message: message, detail: detail };
	}
	async function copyFatal()
	{
		try
		{
			await navigator.clipboard.writeText((fatal.message + "\n" + (fatal.detail || "")).trim());
		}
		catch(e)
		{
			console.warn("copy fatal details failed:", e);
		}
	}
	// ------------------------------------------------------------------
	// Runtime-trap surfacing. The CheerpX core catches guest-side WASM
	// traps (e.g. "memory access out of bounds") at its own trampolines,
	// logs "Unexpected exit <error>" and then either silently kills just
	// that guest process or wedges — cx.run() never rejects, so the fatal
	// overlay above would never fire on its own and the display stays
	// black. These hooks guarantee the failure IS seen: the engine's own
	// report is captured off the console, uncaught engine errors route
	// here, and a boot watchdog gives up on a boot that stops making
	// progress. See plans/webvm_implementation.md §12/21(32).
	var cxDiag = null;          // { message, detail } — the engine's own trap report (if any)
	var cxBootConsoleTail = ""; // rolling tail of the guest boot console (diagnosis aid)
	var guestOutputTs = 0;      // last time the guest wrote to the boot console (ms)
	var bootStarted = false;
	var bootStartTs = 0;
	var lastPixelCheckAt = 0;
	var pixelSeen = false;
	var trapReloadUsed = false; // in-memory guard for the one-shot auto reload
	var watchdogTimer = null;
	var bootElapsed = 0;

	// The runtime reports guest traps as `console.log('Unexpected exit',
	// <RuntimeError>)` (after the vendored runtime patch:
	// `console.error`). Capture the FIRST one so the fatal overlay can
	// show the engine's exact reason, and route it to the overlay right
	// away instead of waiting for the watchdog.
	function installTrapCapture()
	{
		if (typeof window === "undefined" || window.__webvmTrapCaptureInstalled)
			return;
		window.__webvmTrapCaptureInstalled = true;
		var capture = function (args)
		{
			if (!args || !args.length || cxDiag)
				return;
			var msg = args[0];
			if (typeof msg !== "string" || msg.indexOf("Unexpected exit") !== 0)
				return;
			var e = args[1];
			cxDiag = {
				message: e && e.message ? e.message : String(e !== undefined ? e : msg),
				detail: e && e.stack ? e.stack : args.slice(1).map(String).join(" ")
			};
			console.warn("[WebVM] CheerpX engine reported an internal trap:", cxDiag.message);
			reportEngineTrap();
		};
		var wrap = function (orig)
		{
			return function ()
			{
				capture(Array.prototype.slice.call(arguments));
				return orig.apply(console, arguments);
			};
		};
		console.log = wrap(console.log);
		console.error = wrap(console.error);
	}

	// Only CheerpX engine/WASM failures take the whole session to the
	// fatal overlay — unrelated page errors must not.
	function isEngineError(err)
	{
		var text = "";
		if (typeof err === "string")
			text = err;
		else if (err && (err.message || err.stack))
			text = err.message + "\n" + (err.stack || "");
		else if (err)
			text = String(err);
		return /RuntimeError|memory access out of bounds|function signature mismatch|call_indirect|wasm:\/\//.test(text);
	}

	// Build the overlay's error from the captured engine diagnostic, plus
	// the last guest boot output so the user/developer can see WHERE the
	// boot stopped.
	function buildDiagError()
	{
		if (!cxDiag)
		{
			return new Error("The virtual machine stopped unexpectedly inside the CheerpX engine; no further detail was reported.");
		}
		var e = new Error(cxDiag.message);
		e.stack = cxDiag.detail +
			(cxBootConsoleTail ? "\n\nLast guest boot output:\n" + cxBootConsoleTail : "");
		return e;
	}

	// The single funnel for a detected engine trap. `reloadAllowed` marks the
	// call as a DEFINITIVE boot-death signal (a rejecting cx.run() — the run
	// loop is gone — or the watchdog's sustained-silence verdict). Only those
	// may trigger the one-shot auto reload, because the engine's own console
	// trap reports are ambiguous: when it swallows a trap it kills just that
	// guest process and carries on, so a report during boot might not be boot-
	// fatal. Ambiguous reports (the console capture, window errors/unhandled
	// rejections) surface the overlay immediately instead — never a surprise
	// reload of a boot that could still reach the desktop. The reload itself
	// is plain (block cache untouched: a blockCache.reset() would wipe the
	// user's persisted overlay); a second consecutive definitive failure
	// shows the overlay.
	function maybeReportRuntimeTrap(reloadAllowed)
	{
		if (fatal)
			return;
		var allowAutoReload = reloadAllowed && bootStarted && !pixelSeen && !bootedOnce;
		if (allowAutoReload && !trapReloadUsed)
		{
			trapReloadUsed = true;
			try
			{
				if (sessionStorage.getItem("webvm-trap-reload") !== "1" &&
					!sessionStorage.getItem("webvm-test-bootfail") &&
					!sessionStorage.getItem("webvm-test-trapreport"))
				{
					sessionStorage.setItem("webvm-trap-reload", "1");
					location.reload();
					return;
				}
			}
			catch(e)
			{
				// storage blocked — fall through to the overlay
			}
		}
		showFatal(pixelSeen || bootedOnce ? "runtime" : "boot", buildDiagError());
	}

	// The engine's own console trap reports (and window errors / unhandled
	// rejections they surface) are AMBIGUOUS: when the core swallows a WASM
	// trap it logs "Unexpected exit" and carries on — it may have killed
	// only a disposable guest process, in which case the boot still reaches
	// the desktop. So these funnel to the overlay immediately (with the
	// exact reason) but NEVER trigger the one-shot auto reload; only
	// definitive boot-death signals (a rejecting cx.run(), the watchdog)
	// may reload. See maybeReportRuntimeTrap.
	function reportEngineTrap()
	{
		maybeReportRuntimeTrap(false);
	}

	// Uncaught engine errors (e.g. the runtime re-raising a WASM trap as a
	// pageerror) and unhandled promise rejections must also reach the
	// overlay instead of staying in the DevTools console.
	function onWindowError(ev)
	{
		var e = (ev && ev.error) ||
			{ message: (ev && ev.message) || "", stack: (ev && (ev.filename + ":" + ev.lineno)) || "" };
		if (!isEngineError(e))
			return true; // unrelated page error — leave default handling on
		if (!cxDiag)
			cxDiag = { message: e.message || String(e), detail: e.stack || "" };
		reportEngineTrap();
		return false;
	}
	function onUnhandledRejection(ev)
	{
		var e = ev && ev.reason;
		if (!e || !isEngineError(e))
			return;
		if (!cxDiag)
			cxDiag = { message: e.message || String(e), detail: e.stack || "" };
		reportEngineTrap();
	}

	// Cheap version of the E2E's waitForDesktop pixel probe: has the KMS
	// framebuffer rendered anything non-black yet? (256x256 downscale.)
	// Terminal-only VMs have no canvas — the caller gates on needsDisplay,
	// so this probes nothing there.
	function hasDisplayPixels()
	{
		if (!configObj.needsDisplay)
			return false;
		var display = document.getElementById("display");
		if (!display || !display.width || !display.height)
			return false;
		try
		{
			var scratch = document.createElement("canvas");
			scratch.width = Math.min(display.width, 256);
			scratch.height = Math.min(display.height, 256);
			var ctx = scratch.getContext("2d");
			ctx.drawImage(display, 0, 0, scratch.width, scratch.height);
			var data = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
			for(var i = 0; i < data.length; i += 4)
			{
				if(data[i] || data[i+1] || data[i+2])
					return true;
			}
		}
		catch(e)
		{
			// canvas not readable yet — not an error
		}
		return false;
	}

	// Boot watchdog: a boot that makes no progress (no guest console
	// output AND no display pixels) for a long stretch is a silent halt
	// (trapped boot process, wedged core). Declare it stuck only after a
	// generous floor, and never while the tab is hidden (background tab
	// throttling makes real boots look silent). The thresholds stay ABOVE
	// the project's own boot-readiness budget (240 s first-pixel timeout in
	// tests/e2e/lib/desktop.js) so a slow-but-successful boot — e.g. a cold
	// cache streaming the disk image — can never be declared stuck by the
	// page while the E2E definition would accept it.
	var STUCK_SILENT_MS = 200000; // continuous silence, no floor yet included
	var STUCK_FLOOR_MS = 270000;  // never declare stuck before this since the run started
	var WATCHDOG_INTERVAL_MS = 2000;

	function watchdogTick()
	{
		if (fatal)
		{
			clearInterval(watchdogTimer);
			watchdogTimer = null;
			return;
		}
		if (pixelSeen || bootedOnce)
		{
			// Boot came up (or a run completed): disarm, and clear the
			// one-shot trap-reload counter so a later, real trap gets its
			// one retry.
			try { sessionStorage.removeItem("webvm-trap-reload"); } catch(e) {}
			clearInterval(watchdogTimer);
			watchdogTimer = null;
			return;
		}
		if (bootStarted)
		{
			var now = Date.now();
			bootElapsed = Math.floor((now - bootStartTs) / 1000);
			if (configObj.needsDisplay && !pixelSeen && now - lastPixelCheckAt > 3000)
			{
				lastPixelCheckAt = now;
				if (hasDisplayPixels())
					pixelSeen = true;
			}
			// Only display VMs get the pixel-based stuck detection. A
			// terminal-only VM has no canvas, so a user idling at a shell
			// must not look "stuck" — trap capture + global handlers still
			// cover its failure modes.
			if (configObj.needsDisplay && !pixelSeen &&
				document.visibilityState === "visible" &&
				now - guestOutputTs > STUCK_SILENT_MS &&
				now - bootStartTs > STUCK_FLOOR_MS)
			{
				cxDiag = {
					message: "The virtual machine stopped making progress during boot: no guest activity or display output for a long time. This is the CheerpX engine silently halting (a guest process trapped inside the emulator), not a normal error.",
					detail: (cxDiag ? cxDiag.detail + "\n\n" : "") +
						(cxBootConsoleTail ? "Last guest boot output:\n" + cxBootConsoleTail : "(no guest boot output captured)")
				};
				// Sustained silence is a definitive death signal — the
				// one-shot auto-reload may run (see maybeReportRuntimeTrap).
				maybeReportRuntimeTrap(true);
				clearInterval(watchdogTimer);
				watchdogTimer = null;
			}
		}
	}
	var __bootTextDecoder = null;
	function writeData(buf, vt)
	{
		if(vt != 1)
			return;
		// Watchdog input: the guest is making progress while it writes to
		// the boot console. Keep a rolling tail for the fatal overlay's
		// diagnosis (shows WHERE the boot stopped), but only while the
		// diagnostic window is open (boot, not a whole session of
		// interactive terminal I/O). buf is already a Uint8Array, so no
		// extra copy is needed for the decoder.
		guestOutputTs = Date.now();
		if(!pixelSeen && !bootedOnce && !fatal)
		{
			if(__bootTextDecoder == null)
				__bootTextDecoder = new TextDecoder();
			cxBootConsoleTail = (cxBootConsoleTail + __bootTextDecoder.decode(buf)).slice(-4096);
		}
		term.write(new Uint8Array(buf));
	}
	function readData(str)
	{
		if(cxReadFunc == null)
			return;
		for(var i=0;i<str.length;i++)
			cxReadFunc(str.charCodeAt(i));
	}
	function printMessage(msg)
	{
		for(var i=0;i<msg.length;i++)
			term.write(msg[i] + "\n");
	}
	function expireEvents(list, curTime, limitTime)
	{
		while(list.length > 1)
		{
			if(list[1].t < limitTime)
			{
				list.shift();
			}
			else
			{
				break;
			}
		}
	}
	function cleanupEvents()
	{
		var curTime = Date.now();
		var limitTime = curTime - 10000;
		expireEvents(cpuActivityEvents, curTime, limitTime);
		computeCpuActivity(curTime, limitTime);
		if(cpuActivityEvents.length == 0)
		{
			clearInterval(activityEventsInterval);
			activityEventsInterval = 0;
		}
	}
	function computeCpuActivity(curTime, limitTime)
	{
		var totalActiveTime = 0;
		var lastActiveTime = limitTime;
		var lastWasActive = false;
		for(var i=0;i<cpuActivityEvents.length;i++)
		{
			var e = cpuActivityEvents[i];
			// NOTE: The first event could be before the limit,
			//       we need at least one event to correctly mark
			//       active time when there is long time under load
			var eTime = e.t;
			if(eTime < limitTime)
				eTime = limitTime;
			if(e.state == "ready")
			{
				// Inactive state, add the time from lastActiveTime
				totalActiveTime += (eTime - lastActiveTime);
				lastWasActive = false;
			}
			else
			{
				// Active state
				lastActiveTime = eTime;
				lastWasActive = true;
			}
		}
		// Add the last interval if needed
		if(lastWasActive)
		{
			totalActiveTime += (curTime - lastActiveTime);
		}
		cpuPercentage.set(Math.ceil((totalActiveTime / 10000) * 100));
	}
	function hddCallback(state)
	{
		diskActivity.set(state != "ready");
	}
	function latencyCallback(latency)
	{
		diskLatencies.push(latency);
		if(diskLatencies.length > 30)
			diskLatencies.shift();
		// Average the latency over at most 30 blocks
		var total = 0;
		for(var i=0;i<diskLatencies.length;i++)
			total += diskLatencies[i];
		var avg = total / diskLatencies.length;
		diskLatency.set(Math.ceil(avg));
	}
	function cpuCallback(state)
	{
		cpuActivity.set(state != "ready");
		var curTime = Date.now();
		var limitTime = curTime - 10000;
		expireEvents(cpuActivityEvents, curTime, limitTime);
		cpuActivityEvents.push({t: curTime, state: state});
		computeCpuActivity(curTime, limitTime);
		// Start an interval timer to cleanup old samples when no further activity is received
		if(activityEventsInterval != 0)
			clearInterval(activityEventsInterval);
		activityEventsInterval = setInterval(cleanupEvents, 2000);
	}
	function computeXTermFontSize()
	{
		return parseInt(getComputedStyle(document.body).fontSize);
	}
	function setScreenSize(display)
	{
		var internalMult = 1.0;
		var displayWidth = display.offsetWidth;
		var displayHeight = display.offsetHeight;
		var minWidth = 1024;
		var minHeight = 768;
		if(displayWidth < minWidth)
			internalMult = minWidth / displayWidth;
		if(displayHeight < minHeight)
			internalMult = Math.max(internalMult, minHeight / displayHeight);
		var internalWidth = Math.floor(displayWidth * internalMult);
		var internalHeight = Math.floor(displayHeight * internalMult);
		cx.setKmsCanvas(display, internalWidth, internalHeight);
	}
	// The KMS framebuffer is programmed EXACTLY ONCE, at session start.
	// Post-boot setKmsCanvas calls are broken in CheerpX 1.3.8/1.3.9: the
	// core's worker answers the {type:95,width,height} mode-set with a
	// garbage 320x200 fallback surface (bisect matrix in
	// plans/display-bug.md §"post-boot mode-set regression" — 1.3.7 works,
	// 1.3.8/1.3.9 corrupt). After that first call, viewport resizes are
	// absorbed by CSS scaling of the fixed backing store (the #display box
	// tracks innerWidth-56/innerHeight), which renders correctly and keeps
	// the guest pipeline untouched and live. Re-enable the post-boot call
	// only after an upstream runtime fixes mode-set.

	// Program the KMS framebuffer exactly once per session; see
	// plans/display-bug.md "Post-boot mode-set regression" (1.3.8/1.3.9
	// worker path corrupts the surface on post-boot calls).
	var kmsInitialized = false;
	var curInnerWidth = 0;
	var curInnerHeight = 0;
	function handleResize()
	{
		// Avoid spurious resize events caused by the soft keyboard
		if(curInnerWidth == window.innerWidth && curInnerHeight == window.innerHeight)
			return;
		curInnerWidth = window.innerWidth;
		curInnerHeight = window.innerHeight;
		triggerResize();
	}
	function triggerResize()
	{
		term.options.fontSize = computeXTermFontSize();
		fitAddon.fit();
		const display = document.getElementById("display");
		if(display && cx && !kmsInitialized)
			setScreenSize(display);
	}
	async function initTerminal()
	{
		const { Terminal } = await import('@xterm/xterm');
		const { FitAddon } = await import('@xterm/addon-fit');
		const { WebLinksAddon } = await import('@xterm/addon-web-links');
		term = new Terminal({cursorBlink:true, convertEol:true, fontFamily:"monospace", fontWeight: 400, fontWeightBold: 700, fontSize: computeXTermFontSize()});
		fitAddon = new FitAddon();
		term.loadAddon(fitAddon);
		var linkAddon = new WebLinksAddon();
		term.loadAddon(linkAddon);
		const consoleDiv = document.getElementById("console");
		term.open(consoleDiv);
		// Debug handle for E2E tests: full terminal buffer access (the DOM only
		// exposes the visible rows; the test reads the scrollback for the
		// "no login / no hang" assertions).
		window.__webvmTerm = term;
		term.scrollToTop();
		fitAddon.fit();
		window.addEventListener("resize", handleResize);
		term.focus();
		term.onData(readData);
		// Avoid undesired default DnD handling
		function preventDefaults (e) {
			e.preventDefault()
			e.stopPropagation()
		}
		consoleDiv.addEventListener("dragover", preventDefaults, false);
		consoleDiv.addEventListener("dragenter", preventDefaults, false);
		consoleDiv.addEventListener("dragleave", preventDefaults, false);
		consoleDiv.addEventListener("drop", preventDefaults, false);
		curInnerWidth = window.innerWidth;
		curInnerHeight = window.innerHeight;
		// The display canvas must sit ABOVE the console xterm, or the console
		// intercepts all mouse/keyboard events. The upstream code raises it on
		// guest VT7 activation, but our VT-less X session never activates a VT
		// — and initCheerpX() never returns (it runs the guest in a loop) — so
		// raise it HERE, before the guest starts.
		raiseDisplay();
		if(configObj.printIntro)
			printMessage(introMessage);
		// Boot began: arm the trap/watchdog machinery (the CheerpX core can
		// swallow guest WASM traps and carry on silently — see
		// maybeReportRuntimeTrap / watchdogTick).
		bootStartTs = Date.now();
		guestOutputTs = bootStartTs;
		bootStarted = true;
		if(watchdogTimer == null)
			watchdogTimer = setInterval(watchdogTick, WATCHDOG_INTERVAL_MS);
		await initCheerpX();
	}

	function raiseDisplay()
	{
		// The display canvas must sit ABOVE the console xterm, or the console
		// intercepts all mouse/keyboard events and nothing reaches the guest.
		// The upstream code only does this on guest VT7 activation; our X
		// session is VT-less (see desktop.start), so the console would stay on
		// top forever — raise it unconditionally.
		const display = document.getElementById("display");
		if (display && display.parentElement)
			display.parentElement.style.zIndex = 5;
	}
	function handleActivateConsole(vt)
	{
		if(curVT == vt)
			return;
		curVT = vt;
		if(vt != 7)
			return;
		raiseDisplay();
	}
	function handleProcessCreated()
	{
		processCount++;
		if(processCallback)
			processCallback(processCount);
	}
	// ------------------------------------------------------------------
	// Early boot-resource fetch. The naive chain is strictly sequential:
	// hydration -> terminal setup -> dynamic import of the CheerpX runtime
	// (~2.5 MB JS/WASM) -> disk block-device creation (first ext2 range
	// GETs). Only terminal rendering truly needs to finish first, so start
	// the runtime import and both device creations at mount time,
	// concurrently with terminal setup — initCheerpX then just awaits the
	// finished (or in-flight) promises. On a cold cache this overlaps the
	// xterm chunk load with the CheerpX runtime download and the first
	// image ranges; on a warm cache everything is local and free.
	//
	// Each promise gets an immediate no-op catch: an early rejection must
	// NOT surface as a global unhandledrejection (that routes engine errors
	// into the trap overlay prematurely) but must still propagate when the
	// promise is awaited inside initCheerpX, exactly where the old inline
	// code threw.
	var earlyCheerpx = null;
	var earlyBlockDevice = null;
	var earlyBlockCache = null;
	var earlyFetchStarted = false;
	function guardedPromise(promise)
	{
		promise.catch(function() {});
		return promise;
	}
	function startEarlyBootFetch()
	{
		if(earlyFetchStarted || fatal)
			return;
		earlyFetchStarted = true;
		earlyCheerpx = guardedPromise(import('@leaningtech/cheerpx'));
		earlyBlockCache = guardedPromise(earlyCheerpx.then(function(CheerpX) {
			return CheerpX.IDBDevice.create(cacheId);
		}));
		earlyBlockDevice = guardedPromise(earlyCheerpx.then(function(CheerpX) {
			switch(configObj.diskImageType)
			{
				// NOTE: unlike the old inline chain, the cloud case no longer
				// retries wss:// URLs over https — that branch predates this
				// project and every configured diskImageUrl is already
				// https://same-origin (bytes) or a GitHub chunked image.
				case "cloud":
					return CheerpX.CloudDevice.create(configObj.diskImageUrl);
				case "bytes":
					return CheerpX.HttpBytesDevice.create(configObj.diskImageUrl);
				case "github":
					return CheerpX.GitHubDevice.create(configObj.diskImageUrl);
				default:
					throw new Error("Unrecognized device type");
			}
		}));
	}
	async function initCheerpX()
	{
		const CheerpX = await earlyCheerpx;
		var blockDevice = await earlyBlockDevice;
		// Test-only hook (tests/e2e/tests/error-overlay.spec.js): force a boot
		// failure so the fatal overlay's exact-reason display is assertable.
		if (sessionStorage.getItem("webvm-test-bootfail"))
		{
			throw new Error("test-forced boot failure (webvm-test-bootfail)");
		}
		// Test-only hook: emulate the CheerpX core's swallowed-trap console
		// report (`console.log('Unexpected exit', <err>)`), exercising the
		// interceptor -> fatal-overlay path for a silent guest crash. The
		// marker is consumed on the NEXT navigation by the E2E init script
		// (mirroring the webvm-test-bootfail latch).
		if (sessionStorage.getItem("webvm-test-trapreport"))
		{
			console.log("Unexpected exit", new Error("test-forced engine trap (webvm-test-trapreport)"));
		}
		blockCache = await earlyBlockCache;
		var overlayDevice = await CheerpX.OverlayDevice.create(blockDevice, blockCache);
		var webDevice = await CheerpX.WebDevice.create("");
		var dataDevice = await CheerpX.DataDevice.create();
		var mountPoints = [
			// The root filesystem, as an Ext2 image
			{type:"ext2", dev:overlayDevice, path:"/"},
			// Access to files on the Web server, relative to the current page
			{type:"dir", dev:webDevice, path:"/web"},
			// Access to read-only data coming from JavaScript
			{type:"dir", dev:dataDevice, path:"/data"},
			// Automatically created device files
			{type:"devs", path:"/dev"},
			// Pseudo-terminals
			{type:"devpts", path:"/dev/pts"},
			// The Linux 'proc' filesystem which provides information about running processes
			{type:"proc", path:"/proc"},
			// The Linux 'sysfs' filesystem which is used to enumerate emulated devices
			{type:"sys", path:"/sys"}
		];
		// webdav mode: inject the runtime sync config at /opt/syncrc via a
		// DataDevice (documented writeFile API; paths are relative to the
		// device root, so mounting at /opt and writing "/syncrc" yields the
		// guest path /opt/syncrc). Falls back to the baked /root/.syncrc.
		if (configObj.storageBackend == "webdav")
		{
			var syncUrl = sessionStorage.getItem("syncUrl");
			if (syncUrl)
			{
				var syncrc = "backend = webdav\n" +
					"url = " + syncUrl + "\n" +
					"user = " + (sessionStorage.getItem("syncUser") || "") + "\n" +
					"password = " + (sessionStorage.getItem("syncPass") || "") + "\n";
				try
				{
					var optDevice = await CheerpX.DataDevice.create();
					await optDevice.writeFile("/syncrc", syncrc);
					mountPoints.push({type:"dir", dev:optDevice, path:"/opt"});
				}
				catch(e)
				{
					console.warn("DataDevice syncrc injection failed; the sync agent will use the baked /root/.syncrc", e);
				}
			}
		}
		// Any rejection here propagates to the boot catch in onMount ->
		// showFatal: a boot failure must be visible on screen, not just in
		// the hidden console.
		cx = await CheerpX.Linux.create({mounts: mountPoints, networkInterface: networkInterface});
		cx.registerCallback("cpuActivity", cpuCallback);
		cx.registerCallback("diskActivity", hddCallback);
		cx.registerCallback("diskLatency", latencyCallback);
		cx.registerCallback("processCreated", handleProcessCreated);
		term.scrollToBottom();
		cxReadFunc = cx.setCustomConsole(writeData, term.cols, term.rows);
		const display = document.getElementById("display");
		if(display)
		{
			setScreenSize(display);
			kmsInitialized = true;
			cx.setActivateConsole(handleActivateConsole);
		}
		// Run the command in a loop, in case the user exits. A REJECTED
		// cx.run() is the VM stopping (a resolved run is a guest exit, which
		// is normal and re-runs) — show exactly why it stopped instead of an
		// unhandled promise rejection.
		try
		{
			while (true)
			{
				await cx.run(configObj.cmd, configObj.args, configObj.opts);
				bootedOnce = true;
			}
		}
		catch(e)
		{
			// A rejecting cx.run() is a DEFINITIVE death signal (the run
			// loop is gone) — the one-shot auto-reload is allowed here (the
			// trap is intermittent; the same boot usually succeeds next
			// try). Console-captured reports go through reportEngineTrap
			// instead and never reload.
			if(isEngineError(e))
			{
				cxDiag = { message: e.message || String(e), detail: e.stack || "" };
				maybeReportRuntimeTrap(true);
			}
			else
			{
				showFatal(bootedOnce ? "runtime" : "boot", e);
			}
		}
	}
	onMount(() => {
		// Any error while booting (terminal setup, CheerpX runtime, disk
		// image, Linux.create) surfaces as the visible fatal overlay — a
		// failed load must never be silent.
		installTrapCapture();
		window.addEventListener("error", onWindowError);
		window.addEventListener("unhandledrejection", onUnhandledRejection);
		// The watchdog only judges boot progress while the tab is actually
		// visible (background-throttled boots look silent otherwise). On
		// return, don't count the background time as silence.
		document.addEventListener("visibilitychange", () => {
			if(document.visibilityState === "visible" && bootStarted && !pixelSeen)
				guestOutputTs = Date.now();
		});
		// Start fetching the CheerpX runtime + disk devices NOW, concurrently
		// with terminal setup (see startEarlyBootFetch). initCheerpX awaits
		// these promises after the terminal is ready.
		startEarlyBootFetch();
		initTerminal().catch((e) => { showFatal("boot", e); });
	});
	async function handleConnect()
	{
		const w = window.open("login.html", "_blank");
		cx.networkLogin();
		try
		{
			w.location.href = await startLogin();
		}
		catch(e)
		{
			w.close();
			console.warn(e);
		}
	}
	async function handleReset()
	{
		// Be robust before initialization: a boot-phase failure leaves
		// blockCache null, and the fatal overlay's Reload button must still
		// work (a plain reload is all we can do without a cache to reset).
		if (blockCache == null)
		{
			location.reload();
			return;
		}
		await blockCache.reset();
		location.reload();
	}
	async function handleSidebarPinChange(event)
	{
		sideBarPinned = event.detail;
		// Make sure the pinning state of reflected in the layout
		await tick();
		// Adjust the layout based on the new sidebar state
		triggerResize();
	}
</script>

<main class="relative w-full h-full">
	<div class="absolute top-0 bottom-0 left-0 right-0">
		<SideBar on:connect={handleConnect} on:reset={handleReset} on:sidebarPinChange={handleSidebarPinChange}>
			<slot></slot>
		</SideBar>
		{#if configObj.needsDisplay}
			<div class="absolute top-0 bottom-0 {sideBarPinned ? 'left-[23.5rem]' : 'left-14'} right-0">
				<canvas class="w-full h-full cursor-none" id="display"></canvas>
			</div>
		{/if}
		<div class="absolute top-0 bottom-0 {sideBarPinned ? 'left-[23.5rem]' : 'left-14'} right-0 p-1 scrollbar" id="console">
		</div>
		{#if configObj.needsDisplay && bootStarted && !fatal && !pixelSeen}
			<!-- Boot in progress: the display canvas covers the console, so an
			     unresponsive-looking black screen is honest only if the page
			     says it is still booting. Also the first sign that a boot has
			     quietly died (silently increasing counter). -->
			<div class="absolute top-3 right-3 z-40 rounded bg-black/70 text-green-300 font-mono text-xs px-3 py-1.5 pointer-events-none select-none">
				Booting the VM… {bootElapsed}s
			</div>
		{/if}
	</div>
	{#if fatal}
		<div
			class="absolute top-0 bottom-0 left-0 right-0 z-[100] flex items-center justify-center bg-black/80"
			role="alert"
		>
			<div class="max-w-2xl w-[90%] bg-[#1e1e2e] border border-red-500 rounded-lg p-6 text-white font-mono text-sm shadow-2xl">
				<div class="text-red-400 font-bold text-lg mb-1">
					{fatal.phase === "runtime" ? "The VM stopped unexpectedly" : "The VM failed to start"}
				</div>
				<div class="text-gray-300 mb-3">
					{fatal.phase === "runtime"
						? "The guest session terminated while running."
						: "The browser could not boot the guest."}
					The exact reason is below. If the engine quietly stopped
					mid-boot, Reload usually recovers; the DevTools console
					carries the full stack.
				</div>
				<div class="bg-black rounded p-3 overflow-auto max-h-64 mb-4 whitespace-pre-wrap break-all">
{fatal.message}{#if fatal.detail}
{fatal.detail}{/if}
				</div>
				<div class="flex gap-3">
					<button
						class="px-4 py-2 bg-red-500 hover:bg-red-600 rounded font-semibold"
						on:click={handleReset}
					>
						Reload
					</button>
					<button
						class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded"
						on:click={copyFatal}
					>
						Copy details
					</button>
				</div>
			</div>
		</div>
	{/if}
</main>
