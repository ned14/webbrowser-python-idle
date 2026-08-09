<script>
	import PanelButton from './PanelButton.svelte';
	// TODO(repo-url): the public GitHub repo for this project has not been
	// created/set yet. Replace PROJECT_REPO_URL with the real URL once it is
	// known (e.g. https://github.com/<owner>/<repo>). All links below derive
	// from it, so a single edit covers the panel button, the star link and
	// the issues link.
	// NOTE: the upstream GitHubStarCount component fetches
	// api.github.com/repos/<repo> — an external request this LAN-only
	// deployment must never make — so it is removed.
	const PROJECT_REPO_URL = 'https://github.com/<owner>/<repo>';
</script>

<h1 class="text-lg font-bold">GitHub</h1>
<PanelButton buttonImage="assets/github-mark-white.svg" clickUrl={PROJECT_REPO_URL} buttonText="GitHub repo">
	<i class='fas fa-star fa-xs ml-auto'></i>
</PanelButton>
<p>Like this project? <a class="underline" href={PROJECT_REPO_URL} target="_blank">Give us a star!</a></p>
<p>This project is FOSS, you can fork it to build your own version and begin working on your CheerpX-based project</p>
<p>Found a bug? Please open a <a class="underline" href="{PROJECT_REPO_URL}/issues" target="_blank">GitHub issue</a></p>
