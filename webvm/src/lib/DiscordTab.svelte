<script>
	import PanelButton from './PanelButton.svelte';
	// NOTE: the upstream DiscordPresenceCount component fetches
	// discord.com/api/guilds/<id>/widget.json — an external request this
	// LAN-only deployment must never make — so it is removed.
</script>

<h1 class="text-lg font-bold">Discord</h1>
<PanelButton buttonImage="assets/discord-mark-blue.svg" clickUrl="https://discord.gg/yTNZgySKGa" buttonText="Join our Discord">
	<i class='fas fa-circle fa-xs ml-auto text-green-500'></i>
</PanelButton>
<p>Do you have any question about WebVM or CheerpX?</p>
<p>Join our community, we are happy to help!</p>
