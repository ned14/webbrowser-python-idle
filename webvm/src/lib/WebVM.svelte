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
	function writeData(buf, vt)
	{
		if(vt != 1)
			return;
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
		if(display)
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
	async function initCheerpX()
	{
		const CheerpX = await import('@leaningtech/cheerpx');
		var blockDevice = null;
		switch(configObj.diskImageType)
		{
			case "cloud":
				try
				{
					blockDevice = await CheerpX.CloudDevice.create(configObj.diskImageUrl);
				}
				catch(e)
				{
					// Report the failure and try again with plain HTTP
					var wssProtocol = "wss:";
					if(configObj.diskImageUrl.startsWith(wssProtocol))
					{
						// WebSocket protocol failed, try agin using plain HTTP
						blockDevice = await CheerpX.CloudDevice.create("https:" + configObj.diskImageUrl.substr(wssProtocol.length));
					}
					else
					{
						// No other recovery option
						throw e;
					}
				}
				break;
			case "bytes":
				blockDevice = await CheerpX.HttpBytesDevice.create(configObj.diskImageUrl);
				break;
			case "github":
				blockDevice = await CheerpX.GitHubDevice.create(configObj.diskImageUrl);
				break;
			default:
				throw new Error("Unrecognized device type");
		}
		// Test-only hook (tests/e2e/tests/error-overlay.spec.js): force a boot
		// failure so the fatal overlay's exact-reason display is assertable.
		if (sessionStorage.getItem("webvm-test-bootfail"))
		{
			throw new Error("test-forced boot failure (webvm-test-bootfail)");
		}
		blockCache = await CheerpX.IDBDevice.create(cacheId);
		var overlayDevice = await CheerpX.OverlayDevice.create(blockDevice, blockCache);
		var webDevice = await CheerpX.WebDevice.create("");
		var documentsDevice = await CheerpX.WebDevice.create("documents");
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
			{type:"sys", path:"/sys"},
			// Convenient access to sample documents in the user directory
			{type:"dir", dev:documentsDevice, path:"/home/user/documents"}
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
			showFatal(bootedOnce ? "runtime" : "boot", e);
		}
	}
	onMount(() => {
		// Any error while booting (terminal setup, CheerpX runtime, disk
		// image, Linux.create) surfaces as the visible fatal overlay — a
		// failed load must never be silent.
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
					The exact reason is below; the DevTools console carries the full stack.
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
