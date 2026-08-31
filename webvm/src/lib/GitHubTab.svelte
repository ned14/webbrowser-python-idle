<script>
	import PanelButton from './PanelButton.svelte';
	import { commit, commitDate } from '/config_public_alpine';
	// The public GitHub repo for this project. All links below derive from it,
	// so a single edit covers the panel button, the star link and the issues
	// link.
	// NOTE: the upstream GitHubStarCount component fetches
	// api.github.com/repos/<repo> — an external request this LAN-only
	// deployment must never make — so it is removed.
	const PROJECT_REPO_URL = 'https://github.com/ned14/webbrowser-python-idle';
	// The build's source commit (baked at build time by make/CI via the
	// WEBVM_COMMIT / WEBVM_COMMIT_DATE env vars). Empty on an unversioned dev
	// build — hide the line then rather than link to a bogus commit.
	const buildCommit = commit || null;
</script>

<h1 class="text-lg font-bold">GitHub</h1>
<PanelButton buttonImage="assets/github-mark-white.svg" clickUrl={PROJECT_REPO_URL} buttonText="GitHub repo">
	<i class='fas fa-star fa-xs ml-auto'></i>
</PanelButton>
{#if buildCommit}
	<p>
		This build is from commit
		<a class="underline font-mono" href="{PROJECT_REPO_URL}/commit/{buildCommit}" target="_blank">{buildCommit.slice(0, 7)}</a>
		{#if commitDate} ({commitDate}){/if}
	</p>
{/if}
<p>Like this project? <a class="underline" href={PROJECT_REPO_URL} target="_blank">Give us a star!</a></p>
<p>This project is FOSS, you can fork it to build your own version and begin working on your CheerpX-based project</p>
<p>Found a bug? Please open a <a class="underline" href="{PROJECT_REPO_URL}/issues" target="_blank">GitHub issue</a></p>
