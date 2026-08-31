<script>
	import { networkData, startLogin, updateButtonData, tryStartNetworking, NETWORK_STATES } from '$lib/network.js'
	import { createEventDispatcher } from 'svelte';
	import PanelButton from './PanelButton.svelte';
	var dispatch = createEventDispatcher();
	var connectionState = networkData.connectionState;
	var exitNode = networkData.exitNode;

	function handleConnect() {
		connectionState.set(NETWORK_STATES.DOWNLOADING);
		dispatch('connect');
	}

	// Retry affordance for the UNREACHABLE state (gateway down in a
	// server-only launch): re-probe /health and start the client only if
	// the control plane answered.
	async function handleRetry() {
		connectionState.set(NETWORK_STATES.DOWNLOADING);
		await tryStartNetworking();
	}

	let buttonData = null;
	$: buttonData = updateButtonData(
		$connectionState,
		$connectionState === NETWORK_STATES.UNREACHABLE ? handleRetry : handleConnect
	);
</script>
<h1 class="text-lg font-bold">Networking</h1>
{#if $connectionState === NETWORK_STATES.UNREACHABLE}
	<p class="text-amber-400">This session was launched without a reachable control plane (is the gateway up?). The VM runs, but guest networking is off.</p>
{/if}
<PanelButton buttonImage="assets/tailscale.svg" clickUrl={buttonData.clickUrl} clickHandler={buttonData.clickHandler} rightClickHandler={buttonData.rightClickHandler} buttonTooltip={buttonData.buttonTooltip} buttonText={buttonData.buttonText}>
	{#if $connectionState == NETWORK_STATES.CONNECTED}
		<i class='fas fa-circle fa-xs ml-auto {$exitNode ? 'text-green-500' : 'text-amber-500'}' title={$exitNode ? 'Ready' : 'No exit node'}></i>
	{/if}
</PanelButton>
<p>WebVM can connect to the Internet via Tailscale</p>
<p>Using Tailscale is required since browser do not support TCP/UDP sockets (yet!)</p>
