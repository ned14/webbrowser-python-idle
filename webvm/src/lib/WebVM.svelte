<script>
	import { onMount, tick } from 'svelte';
	import SideBar from '$lib/SideBar.svelte';
	import '$lib/global.css';
	import '@xterm/xterm/css/xterm.css'
	import '@fortawesome/fontawesome-free/css/all.min.css'
	import { networkInterface, startLogin } from '$lib/network.js'
	import { sampleCanvasPixels, hasAnyPixel } from '$lib/canvasProbe.js'
	import { cpuActivity, diskActivity, cpuPercentage, diskLatency } from '$lib/activities.js'
	import { introMessage } from '$lib/messages.js'
	import { pasteStatus, pasteUntypableReason, encodePasteFrame, pasteAckTimeoutMs, consumePasteAcks, PASTE_MAX_CHARS } from '$lib/clipboard.js'

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
	// "Estimated time remaining" boot pill: the guest's file manager writes
	// 'webvm desktop ready' to /dev/console once its first listing is on
	// screen, and the pill counts down from an ETA until that marker lands.
	// The ETA is SCALED by the engine's measured per-block read latency (the
	// same "Backend latency" the Disk pane displays). Recalibrated
	// 2026-09-02 for the Cloudflare-fronted deployment (webvm.nedprod.com):
	// the CDN proxies every byte-range request to the origin (206s are not
	// edge-cached), adding a fixed ~15-20 ms per block read, so the
	// measured profile is now 57.8 s boot at a 73 ms steady read latency
	// (UK) and 70.4 s at 96 ms (+90 ms throttle) — ~0.55 s of boot per extra
	// ms — vs the pre-CDN origin-direct points the 105 s/57 ms/0.75 model
	// was fitted to (52.5 s at 57 ms; 86.8 s at 103 ms). The anchor moves to
	// the CDN reference and the slope is set slightly above the measured
	// 0.55 for headroom; latency below the reference never shrinks the ETA
	// below the floor (cache-warm or edge-cached reads).
	var BOOT_ETA_SECONDS = 75;
	var LATENCY_REF_MS = 73;          // measured steady read latency through Cloudflare
	var BOOT_ETA_LATENCY_SCALE = 0.6; // s of boot per extra ms of read latency (CDN proxy leg)
	var BOOT_ETA_MIN_SECONDS = 60;
	var BOOT_ETA_MAX_SECONDS = 300;
	var DESKTOP_READY_MARKER = "webvm desktop ready";
	var fileManagerSeen = false;
	var bootRemaining = BOOT_ETA_SECONDS;
	var etaTimer = null;

	function dynamicBootEtaSeconds()
	{
		var lat = Math.max($diskLatency, LATENCY_REF_MS);
		return Math.max(BOOT_ETA_MIN_SECONDS, Math.min(BOOT_ETA_MAX_SECONDS,
			BOOT_ETA_SECONDS + (lat - LATENCY_REF_MS) * BOOT_ETA_LATENCY_SCALE));
	}

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
	// loop is gone — the watchdog's sustained-silence verdict, or an engine
	// "Unexpected exit" report before the desktop marker). Those may trigger
	// the one-shot auto reload. Local reproduction (2026-09-01) has shown
	// EVERY observed "Unexpected exit" during boot to be fatal for this
	// image (the boot deadlocked/stalled in every trapped run — the trap at
	// ~4 s and silent stalls at ~14 s both ended with a canvas that never
	// rendered), so a pre-desktop trap report is treated as definitive
	// death rather than ambiguous. The reload itself is plain (block cache
	// untouched: a blockCache.reset() would wipe the user's persisted
	// overlay); a second consecutive definitive failure shows the overlay.
	function maybeReportRuntimeTrap(reloadAllowed)
	{
		if (fatal)
			return;
		var allowAutoReload = reloadAllowed && bootStarted && !fileManagerSeen;
		// Never auto-reload once the desktop marker was seen: a trap in an
		// established session shows the overlay (a live session cannot be
		// resumed by restarting the boot anyway).
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
	// rejections they surface) are treated as DEFINITIVE boot death while
	// the desktop has not appeared yet (local reproduction: every trapped
	// boot stalled — see maybeReportRuntimeTrap). `fileManagerSeen` — NOT
	// `bootedOnce` (which stays false for the whole first session, since
	// cx.run() only resolves when the guest exits) — marks "desktop is up",
	// so the one-shot auto-reload is allowed only for pre-desktop traps; a
	// trap after the desktop is up shows the overlay
	// (reportEngineTrap -> maybeReportRuntimeTrap(false)).
	function reportEngineTrap()
	{
		maybeReportRuntimeTrap(bootStarted && !fileManagerSeen);
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
	// The watchdog's pixel probe uses ONE reused scratch canvas (a fresh
	// 256x256 canvas per tick is allocation + GC churn on the boot critical
	// path; the probe itself only needs a readable downscale). The sampling
	// itself is the SHARED canvasProbe.js implementation — the E2E probes
	// run the same code (tests/e2e/lib/desktop.js injects the module).
	var watchdogScratchCanvas = null;
	function hasDisplayPixels()
	{
		if (!configObj.needsDisplay)
			return false;
		var display = document.getElementById("display");
		if (!display || !display.width || !display.height)
			return false;
		if (watchdogScratchCanvas == null)
			watchdogScratchCanvas = document.createElement("canvas");
		return hasAnyPixel(sampleCanvasPixels(display, { scratch: watchdogScratchCanvas }));
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
	// 5 s tick (was 2 s): each tick does a full-canvas getImageData readback
	// when the display is up, and the boot phase is the heaviest guest load —
	// a slower tick costs nothing on the stuck verdict (the thresholds are
	// 200/270 s) while cutting the GPU readback churn in half.
	var WATCHDOG_INTERVAL_MS = 5000;

	// Fast stuck recovery for pre-desktop deaths does NOT go through this
	// watchdog: an engine "Unexpected exit" trap report during boot reloads
	// immediately via reportEngineTrap -> maybeReportRuntimeTrap (2026-09-01
	// reproduction: every pre-desktop trap was fatal). The watchdog here is
	// only the backstop for silent halts that never report a trap.

	// Repeat-boot warm (post-boot, idle): after the desktop is up, stream the
	// ENTIRE image into the browser HTTP cache (immutable-cached, so no
	// revalidation) — a later session on this machine then reads EVERY block
	// from the disk cache, not just the boot-critical leading bytes warmed in
	// startEarlyBootFetch. One low-priority request; any failure just means
	// the next boot reads from the network as before. bytes-mode only (the
	// GitHub Pages deployment streams chunks via GitHubDevice instead).
	var fullWarmDone = false;
	function maybeWarmFullImage()
	{
		if(fullWarmDone || !configObj || configObj.diskImageType !== "bytes" || !configObj.diskImageUrl)
			return;
		fullWarmDone = true;
		try
		{
			fetch(configObj.diskImageUrl, { priority: "low" }).catch(function() {});
		}
		catch(e) { /* ignore: the warm fetch is an optimization only */ }
	}

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
			// The desktop is up and the engine is idle-ish: warm the whole
			// image for the next session (one low-priority fetch, never on
			// the boot critical path).
			maybeWarmFullImage();
			return;
		}
		if (bootStarted)
		{
			var now = Date.now();
			bootElapsed = Math.floor((now - bootStartTs) / 1000);
			if (configObj.needsDisplay && !pixelSeen && now - lastPixelCheckAt > 5000)
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
	var __pasteTextDecoder = null;
	var __desktopTextDecoder = null;

	// 1-second countdown ticker for the boot pill ("Estimated time
	// remaining"). Self-terminates once the file manager reports ready, a
	// fatal lands, or the boot no longer runs — the watchdog's 5 s tick keeps
	// its own (slower) cadence for stuck detection.
	function fmtClock(s)
	{
		s = Math.max(0, Math.floor(s));
		return String(Math.floor(s / 60)).padStart(2, "0") + ":" +
			String(s % 60).padStart(2, "0");
	}
	function startEtaTimer()
	{
		if(etaTimer != null)
			return;
		etaTimer = setInterval(() => {
			if(fatal || fileManagerSeen || !bootStarted)
			{
				clearInterval(etaTimer);
				etaTimer = null;
				return;
			}
			var eta = Math.max(BOOT_ETA_MIN_SECONDS,
				dynamicBootEtaSeconds());
			bootRemaining = Math.max(0, eta -
				Math.floor((Date.now() - bootStartTs) / 1000));
		}, 1000);
	}
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
			// stream:true — a multi-byte UTF-8 char split across console
			// chunks must not decode as U+FFFD in the fatal-overlay tail
			// (same streaming treatment the paste-ack scanner below gets).
			if(__bootTextDecoder == null)
				__bootTextDecoder = new TextDecoder("utf-8", {fatal:false});
			cxBootConsoleTail = (cxBootConsoleTail + __bootTextDecoder.decode(buf, {stream:true})).slice(-4096);
		}
		// Desktop-ready marker: run REGARDLESS of pixelSeen — the file manager
		// reports itself on the boot console only after the first pixels — so
		// the boot pill stays honest until the desktop is actually up.
		if(bootStarted && !fileManagerSeen && !fatal)
		{
			if(__desktopTextDecoder == null)
				__desktopTextDecoder = new TextDecoder("utf-8", {fatal:false});
			if(__desktopTextDecoder.decode(buf, {stream:true}).indexOf(DESKTOP_READY_MARKER) !== -1)
				fileManagerSeen = true;
		}
		// The paste typer's CXACK/CXFAIL answers ride the console stream;
		// scan them off (stream:true so multi-byte UTF-8 split across
		// chunks stays intact — the frames themselves are ASCII).
		if(__pasteTextDecoder == null)
			__pasteTextDecoder = new TextDecoder("utf-8", {fatal:false});
		scanPasteAck(__pasteTextDecoder.decode(buf, {stream:true}));
		term.write(new Uint8Array(buf));
	}
	function readData(str)
	{
		if(cxReadFunc == null)
			return;
		for(var i=0;i<str.length;i++)
			cxReadFunc(str.charCodeAt(i));
	}
	// ------------------------------------------------------------------
	// Paste (sidebar Clipboard panel). The text is sent to the guest as a
	// `CXCLIP <len> <base64>` frame over the console input channel; the
	// guest paste-typer (/usr/local/bin/paste-typer.sh) types it into the
	// X-input-focus window via xsendkeys (XTEST fake input) — literally the
	// same key events as if the user had typed the text by hand — and
	// answers `CXACK <len>` on the console, which releases the single
	// in-flight throttle. Because it is real keystroke input, only text
	// that CAN be typed is accepted: printable ASCII plus \n \t \b;
	// anything else (control characters, all non-ASCII — é, “smart
	// quotes”, 日本語, emoji) is REFUSED with a diagnostic naming the
	// offending character, both here and in the typer.
	// ------------------------------------------------------------------
	// The wire contract (typability gate, frame encoding, ack timeout and
	// ack-line scanning) lives in clipboard.js — the SAME module PasteTab
	// uses for PASTE_MAX_CHARS/CX_TYPE_DELAY_MS — so the page-side protocol
	// is unit-tested against the guest contract (tests/unit/test_paste_typer.py
	// pins the guest side).
	var pasteInFlight = false;
	var pasteTimeout = null;
	var pasteAckBuf = "";
	function releasePasteThrottle(acked, len)
	{
		pasteInFlight = false;
		if(pasteTimeout)
		{
			clearTimeout(pasteTimeout);
			pasteTimeout = null;
		}
		if(acked)
			pasteStatus.set("Pasted into the VM (" + len + " chars, typed as keys)");
		else if(acked === false)
			pasteStatus.set("Paste failed in the VM — see the console for the reason");
		else
			pasteStatus.set("");
	}
	function scanPasteAck(text)
	{
		// The guest typer answers CXACK/CXFAIL on the console; release the
		// in-flight throttle when they arrive (lines may span chunks, so
		// reassemble; only CX-prefixed fragments are held back).
		pasteAckBuf = consumePasteAcks(pasteAckBuf + text, {
			onAck: function(len) { releasePasteThrottle(true, len); },
			onFail: function() { releasePasteThrottle(false); },
		});
	}
	function sendPasteText(text)
	{
		if(pasteInFlight || text == null || text === "")
			return;
		if(text.length > PASTE_MAX_CHARS)
		{
			pasteStatus.set("Not pasted — larger than " + PASTE_MAX_CHARS + " characters");
			return;
		}
		var bad = pasteUntypableReason(text);
		if(bad)
		{
			console.warn("clipboard paste refused: cannot be typed as keys: " + bad);
			pasteStatus.set("Not pasted — cannot be typed as keys: " + bad);
			return;
		}
		if(cxReadFunc == null)
		{
			pasteStatus.set("VM not ready — paste again once booted");
			return;
		}
		pasteInFlight = true;
		pasteStatus.set("Pasting into the VM…");
		pasteTimeout = setTimeout(releasePasteThrottle, pasteAckTimeoutMs(text.length));
		// readData is the console-input writer (the same loop the terminal's
		// own onData uses) — one send path, not a copied second loop.
		readData(encodePasteFrame(text));
	}
	function handleSidebarPaste(e)
	{
		sendPasteText(e.detail && e.detail.text || "");
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
	// Coalesce the disk-latency average: latencyCallback fires per block read
	// (dense during boot); recompute the 30-entry average + store update at
	// most every 500 ms — far finer than the sidebar UI needs (same pattern
	// as the CPU-percentage coalescing above).
	var diskLatencyLastComputeAt = 0;
	function latencyCallback(latency)
	{
		diskLatencies.push(latency);
		if(diskLatencies.length > 30)
			diskLatencies.shift();
		var curTime = Date.now();
		if(curTime - diskLatencyLastComputeAt < 500)
			return;
		diskLatencyLastComputeAt = curTime;
		// Average the latency over at most 30 blocks
		var total = 0;
		for(var i=0;i<diskLatencies.length;i++)
			total += diskLatencies[i];
		var avg = total / diskLatencies.length;
		diskLatency.set(Math.ceil(avg));
	}
	// Coalesce the CPU-percentage recomputation: cpuCallback fires on EVERY
	// engine scheduling event, and each call used to re-walk the whole 10 s
	// sample window plus churn the 2 s cleanup interval — a busy guest
	// triggered this hundreds of times per second. The percentage is now
	// recomputed at most every 500 ms (cleanupEvents still recomputes on its
	// 2 s tick), which is far finer than the sidebar UI needs. The cleanup
	// timer is armed ONCE (guarded): the old clearInterval+setInterval per
	// event was itself timer churn on the boot critical path.
	var cpuLastComputeAt = 0;
	function cpuCallback(state)
	{
		cpuActivity.set(state != "ready");
		var curTime = Date.now();
		var limitTime = curTime - 10000;
		expireEvents(cpuActivityEvents, curTime, limitTime);
		cpuActivityEvents.push({t: curTime, state: state});
		if(curTime - cpuLastComputeAt >= 500)
		{
			cpuLastComputeAt = curTime;
			computeCpuActivity(curTime, limitTime);
		}
		// Start an interval timer to cleanup old samples when no further activity is received
		if(activityEventsInterval == 0)
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
		// Cap the KMS backing store: EVERYTHING in the guest scales with the
		// framebuffer (Xorg ShadowFB blits during boot, Tk/IDLE rendering,
		// the runtime's per-frame canvas transfer) — an uncapped 1920x1080
		// window is ~2.6x the pixels of 1024x768. The #display CSS box
		// scales the canvas up to the window, so a capped internal
		// resolution is invisible to the user (verified by the resize E2E).
		var maxWidth = 1280;
		var maxHeight = 800;
		if(displayWidth * internalMult > maxWidth)
			internalMult = maxWidth / displayWidth;
		if(displayHeight * internalMult > maxHeight)
			internalMult = Math.min(internalMult, maxHeight / displayHeight);
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
		bootRemaining = BOOT_ETA_SECONDS;
		startEtaTimer();
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
		// Cold-boot overlap: the ext2's actual block reads only start at
		// cx.run() (after the engine worker + wasm compile), so on a cold
		// cache the network sits idle while the engine initializes, then the
		// guest's first reads trigger ~1100 sequential range GETs that
		// compete with the emulator's critical path. Warm the image's
		// LEADING bytes into the HTTP cache during engine init instead: the
		// boot-critical blocks (bootloader, init, openrc, Xorg, the Python
		// stdlib the explorer/IDLE import) live at the start of the image,
		// and the browser HTTP cache serves the guest's overlapping range
		// requests from a cached 206 — first reads become cache hits.
		// 32 MiB (was 16): the boot-critical read set measurably extends
		// past the first 16 MiB on the emulated i386; one low-priority range
		// request costs nothing when the image is cached. Same URL (incl.
		// the ?v= fingerprint) so the cache key is shared with
		// HttpBytesDevice's reads. Fire-and-forget: any failure just means
		// the guest reads from the network as before. (Chrome supports fetch
		// priority hints; other engines ignore it.) Kept at 32 MiB — a
		// larger warm competed with the wasm client's own cold-boot
		// download on the shared connection (2026-08-30).
		if(configObj.diskImageType === "bytes" && configObj.diskImageUrl)
		{
			try
			{
				fetch(configObj.diskImageUrl, {
					headers: { Range: "bytes=0-33554432" },
					priority: "low",
				}).catch(function() {});
			}
			catch(e) { /* ignore: the warm fetch is an optimization only */ }
		}
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
				maybeWarmFullImage();
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
		// The panel dispatches 'connect' only while the sidebar is
		// interactive, but a click can still land while the VM is booting
		// (or after a fatal): cx.networkLogin() would throw on a null cx and
		// the login popup would be stranded on "Loading network code…".
		if(!cx)
		{
			console.warn("network login unavailable while the VM is not running");
			return;
		}
		const w = window.open("login.html", "_blank");
		if(!w)
			return;
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
	// The fatal overlay's Reload is RECOVERY, not factory reset: a plain
	// reload (exactly what the trap auto-reload path does). Wiping the
	// IndexedDB overlay here — the ONLY persistence in browser mode — would
	// silently delete the user's files on every engine hiccup (the old
	// handleReset path). The sidebar Disk-tab Reset keeps the documented
	// cache wipe.
	function handleReload()
	{
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
		<SideBar on:connect={handleConnect} on:reset={handleReset} on:sidebarPinChange={handleSidebarPinChange} on:paste={handleSidebarPaste}>
			<slot></slot>
		</SideBar>
		{#if configObj.needsDisplay}
			<div class="absolute top-0 bottom-0 {sideBarPinned ? 'left-[23.5rem]' : 'left-14'} right-0">
				<canvas class="w-full h-full cursor-none outline-none" id="display" tabindex="0"></canvas>
			</div>
		{/if}
		<div class="absolute top-0 bottom-0 {sideBarPinned ? 'left-[23.5rem]' : 'left-14'} right-0 p-1 scrollbar" id="console">
		</div>
		{#if configObj.needsDisplay && bootStarted && !fatal && !fileManagerSeen}
			<!-- Boot pill: the display canvas covers the console, so an
			     unresponsive-looking black screen is honest only if the page
			     says it is still booting. Counts DOWN from an estimate until
			     the guest's file manager reports itself ready (the
			     'webvm desktop ready' console marker — file-explorer.py), so
			     it stays visible through the pixel->desktop window; a stuck
			     boot floors at 00:00 (the watchdog still handles it). -->
			<div class="absolute top-3 right-3 z-40 rounded bg-black/70 text-green-300 font-mono text-xs px-3 py-1.5 pointer-events-none select-none">
				Estimated time remaining {fmtClock(bootRemaining)}
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
						on:click={handleReload}
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
