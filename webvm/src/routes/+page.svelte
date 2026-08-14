<script>
	import { onMount } from 'svelte';

	// The project website IS the Alpine desktop (alpine.html). The local nginx
	// server already redirects / -> /alpine.html (nginx.conf.template) and
	// GitHub Pages has no nginx, so do the same here with a RELATIVE redirect
	// that works under any base path (a GitHub Pages project site lives under
	// /<repo>/, where a root-absolute /alpine.html would 404).
	onMount(() => {
		location.replace('alpine.html');
	});
</script>

<p style="padding:16px; font-family:monospace;">Loading the desktop…</p>
