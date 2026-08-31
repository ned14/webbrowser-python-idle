<script>
	import { resetCountdown } from './resetCountdown.js';
	import { formatCountdown } from './resetCountdown.js';
</script>

{#if $resetCountdown !== null}
	<div class="pt-1 pb-1" title="This instance resets its storage periodically — unsaved changes are lost when it does.">
		{#if $resetCountdown > 0}
			<span>Storage resets in <span class="font-mono">{formatCountdown($resetCountdown)}</span></span>
		{:else}
			<span>Storage reset imminent</span>
		{/if}
	</div>
{/if}
