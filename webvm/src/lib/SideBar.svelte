<script>
	import { createEventDispatcher } from 'svelte';
	import Icon from './Icon.svelte';
	import InformationTab from './InformationTab.svelte';
	import NetworkingTab from './NetworkingTab.svelte';
	import CpuTab from './CpuTab.svelte';
	import DiskTab from './DiskTab.svelte';
	import GitHubTab from './GitHubTab.svelte';
	import SmallButton from './SmallButton.svelte';
	import { cpuActivity, diskActivity } from './activities.js';
	import { networkReachable, networkingEnabled } from './network.js';
	import { filePickerActive } from './clipboard.js';
	import PasteTab from './PasteTab.svelte';
	const icons = [
		{ icon: 'fas fa-info-circle', info: 'Information', activity: null },
		{ icon: 'fas fa-wifi', info: 'Networking', activity: null },
		{ icon: 'fas fa-microchip', info: 'CPU', activity: cpuActivity },
		{ icon: 'fas fa-compact-disc', info: 'Disk', activity: diskActivity },
		{ icon: 'fas fa-clipboard', info: 'Clipboard', activity: null },
		null,
		{ icon: 'fab fa-github', info: 'GitHub', activity: null },
	];
	let dispatch = createEventDispatcher();
	let activeInfo = null; // Tracks currently visible info.
	let hideTimeout = 0; // Timeout for hiding info panel.
	export let sideBarPinned;

	function showInfo(info) {
		clearTimeout(hideTimeout);
		hideTimeout = 0;
		activeInfo = info;
	}
	function hideInfo() {
		// Never remove the sidebar if pinning is enabled
		if(sideBarPinned)
			return;
		// Keep the panel open while a native file picker is (or may be) up:
		// the picker flow makes the browser fire a synthetic mouseleave when
		// it closes, which would otherwise close the panel mid-flow and drop
		// the chosen file. The flag is cleared shortly after the picker's
		// change/cancel events, so genuine hover-away still closes normally.
		if($filePickerActive)
			return;
		// Prevents multiple timers and hides the info panel after 400ms unless interrupted.
		clearTimeout(hideTimeout);
		hideTimeout = setTimeout(() => {
			activeInfo = null;
			hideTimeout = 0;
		}, 400);
	}
	function handleMouseEnterPanel() {
		clearTimeout(hideTimeout);
		hideTimeout = 0;
	}
	// Toggles the info panel for the clicked icon.
	function handleClick(icon) {
		if(sideBarPinned)
			return;
		// Disabled entries (Networking without a tailnet deployment) cannot
		// open their panel — the icon already suppresses its own events.
		if(icon.disabled)
			return;
		// Hides the panel if the icon is active. Otherwise, shows the panel with info.
		if (activeInfo === icon.info) {
			activeInfo = null;
		} else {
			activeInfo = icon.info;
		}
	}

	function toggleSidebarPin() {
		sideBarPinned = !sideBarPinned;
		dispatch('sidebarPinChange', sideBarPinned);
	}
</script>

<div class="flex flex-row w-14 h-full bg-neutral-700" >
	<div class="flex flex-col shrink-0 w-14 text-gray-300">
		{#each icons as i}
			{#if i}
				<Icon
					icon={i.icon}
					info={i.info}
					activity={i.activity}
					disabled={i.info === 'Networking' && !$networkReachable}
					inert={i.info === 'Networking' && !networkingEnabled}
					on:mouseover={(e) => showInfo(e.detail)}
					on:click={() => handleClick(i)}
				/>
			{:else}
				<div class="grow" on:mouseenter={handleMouseEnterPanel}></div>
			{/if}
		{/each}
	</div>
	<div
		class="relative flex flex-col gap-5 shrink-0 w-80 h-full z-10 p-2 bg-neutral-600 text-gray-100 opacity-95"
		class:hidden={!activeInfo}
		on:mouseenter={handleMouseEnterPanel}
		on:mouseleave={hideInfo}
	>
		<div class="absolute right-2 top-2">
			<SmallButton
				buttonIcon="fa-solid fa-thumbtack"
				clickHandler={toggleSidebarPin}
				buttonTooltip={sideBarPinned ? "Unpin Sidebar" : "Pin Sidebar"}
				bgColor={sideBarPinned ? "bg-neutral-500" : "bg-neutral-700"}
			/>
		</div>
		{#if activeInfo === 'Information'}
			<InformationTab>
				<slot></slot>
			</InformationTab>
		{:else if activeInfo === 'Networking'}
			<NetworkingTab on:connect/>
		{:else if activeInfo === 'CPU'}
			<CpuTab/>
		{:else if activeInfo === 'Disk'}
			<DiskTab on:reset/>
		{:else if activeInfo === 'Clipboard'}
			<PasteTab on:paste/>
		{:else if activeInfo === 'GitHub'}
			<GitHubTab/>
		{:else}
			<p>TODO: {activeInfo}</p>
		{/if}

		<div class="mt-auto text-sm text-gray-300">
			<div class="pt-1 pb-1">
				<a href="https://cheerpx.io/" target="_blank">
					<span>Powered by CheerpX</span>
					<img src="assets/cheerpx.svg" alt="CheerpX Logo" class="w-6 h-6 inline-block">
				</a>
			</div>
			<hr class="border-t border-solid border-gray-300">
			<div class="pt-1 pb-1">
				<a href="https://leaningtech.com/" target="”_blank”">© 2022-2025 Leaning Technologies</a>
			</div>
		</div>
	</div>
</div>
