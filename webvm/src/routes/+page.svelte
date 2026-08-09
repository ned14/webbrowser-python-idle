<script>
import WebVM from '$lib/WebVM.svelte';
import * as configObj from '/config_terminal';
function handleProcessCreated(processCount)
{
}
</script>

<WebVM configObj={configObj} processCallback={handleProcessCreated} cacheId="blocks_terminal">
	<p>Looking for a complete desktop experience? Try the <a class="underline" href="/alpine.html" target="_blank">Alpine Linux</a> graphical WebVM</p>
</WebVM>
