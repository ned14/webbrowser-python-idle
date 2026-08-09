<script>
	import PanelButton from './PanelButton.svelte';
	// NOTE: the upstream GitHubStarCount component fetches
	// api.github.com/repos/<repo> — an external request this LAN-only
	// deployment must never make — so it is removed.
</script>

<h1 class="text-lg font-bold">GitHub</h1>
<PanelButton buttonImage="assets/github-mark-white.svg" clickUrl="https://github.com/leaningtech/webvm" buttonText="GitHub repo">
	<i class='fas fa-star fa-xs ml-auto'></i>
</PanelButton>
<p>Like WebVM? <a class="underline" href="https://github.com/leaningtech/webvm" target="_blank">Give us a star!</a></p>
<p>WebVM is FOSS, you can fork it to build your own version and begin working on your CheerpX-based project</p>
<p>Found a bug? Please open a <a class="underline" href="https://github.com/leaningtech/webvm/issues" target="_blank">GitHub issue</a></p>
