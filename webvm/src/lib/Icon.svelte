<script>
	export let icon;
	export let info;
	export let activity;
	// Visual cross-out + dimming (this entry cannot work right now).
	export let disabled = false;
	// Event suppression (no panel ever — used when the entry could not
	// work EVEN IN PRINCIPLE, e.g. a build without any tailnet support).
	// Crossed-out-but-clickable is the "reachable-gateway-down" case: the
	// panel explains why and offers Retry.
	export let inert = false;
	import { createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();
	function handleMouseover() {
		if(inert)
			return;
		dispatch('mouseover', info);
	}
	function handleClick() {
		if(inert)
			return;
		dispatch('click', { icon, info });
	}
</script>

<div
	class="relative p-3 text-center {$activity ? "text-amber-500 animate-pulse" : ""} {disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer hover:bg-neutral-600 hover:text-gray-100"}"
	style="animation-duration: 0.5s"
	on:mouseenter={handleMouseover}
	on:click={handleClick}
	title={disabled ? `${info} is not available${inert ? " in this deployment" : " until the control plane answers"}` : null}
	aria-disabled={disabled}
>
	<i class='{icon} fa-xl'></i>
	{#if disabled}
		<!-- Red cross-out overlay: this entry cannot work right now. -->
		<div class="absolute inset-0 pointer-events-none" aria-hidden="true">
			<div class="absolute bg-red-600 rounded" style="left:50%;top:50%;width:26px;height:3px;transform:translate(-50%,-50%) rotate(45deg);"></div>
			<div class="absolute bg-red-600 rounded" style="left:50%;top:50%;width:26px;height:3px;transform:translate(-50%,-50%) rotate(-45deg);"></div>
		</div>
	{/if}
</div>
