<script>
	import { createEventDispatcher } from 'svelte';
	import PanelButton from './PanelButton.svelte';
	import { pasteStatus, pasteText, filePickerActive, PASTE_MAX_CHARS, CX_TYPE_DELAY_MS } from '$lib/clipboard.js';
	var dispatch = createEventDispatcher();
	var fileInput = null;

	// The guest types at CX_TYPE_DELAY_MS per char (paste-typer.sh), so
	// a long paste takes a while; warn the user with a time estimate before
	// they commit. PASTE_MAX_CHARS is the page-side hard cap — the SAME
	// constant WebVM.svelte enforces (exported from clipboard.js).
	var LONG_WARN_CHARS = 300;

	// This page NEVER claims the host browser's clipboard API
	// (navigator.clipboard is untouched). Pasting into the VM goes through
	// the textarea below — the browser's OWN native Ctrl+V / right-click
	// paste works inside it without any permission.
	function pasteToVM() {
		dispatch('paste', { text: $pasteText });
	}

	// --- File loading (Open file… button + drag-and-drop onto the box) ---
	// The file is read as TEXT into the textarea; whatever the paste
	// machinery already enforces (ASCII-only typing, the 10000-char cap)
	// then applies exactly as if the text had been typed in by hand.
	function readFile(file) {
		if (!file) return;
		var reader = new FileReader();
		reader.onload = function() {
			$pasteText = String(reader.result || "");
		};
		reader.onerror = function() {
			pasteStatus.set("Could not read the file");
		};
		reader.readAsText(file);
	}
	// The guard is cleared ONLY when the picker is KNOWN closed — the
	// input's change (file chosen) / cancel (dismissed) events, or the
	// first page pointerdown (native pickers are modal: while the dialog
	// is open the page receives no pointer events, so a pointerdown proves
	// it closed). NO timer and NO window-focus: Chrome fires a window
	// `focus` event while the dialog is still open (focus churn when the
	// picker opens), which cleared the guard early and let the later
	// mouseleave close the panel — the exact regression this design fixes.
	function onFilePicked(e) {
		readFile(e.target.files && e.target.files[0]);
		e.target.value = ""; // allow re-picking the same file
		filePickerActive.set(false);
	}
	function onFileCancelled() {
		filePickerActive.set(false);
	}
	function onWindowPointerDown() {
		// First page interaction while the guard is set: the modal dialog
		// must have closed (covers browsers that never fire `cancel`).
		if($filePickerActive)
			filePickerActive.set(false);
	}
	function openFileDialog() {
		if (!fileInput) return;
		// Keep the panel open while the native picker is up: the sidebar
		// auto-closes on hover-away, and moving the mouse over to the
		// dialog fires that mouseleave — the panel would close and drop
		// the chosen file.
		filePickerActive.set(true);
		fileInput.click();
	}
	function onDragOver(e) {
		e.preventDefault(); // allow the drop
	}
	function onDrop(e) {
		e.preventDefault();
		readFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
	}

	// --- Length warning ---
	// The guest types at CX_TYPE_DELAY_MS per char; ~200 chars/sec at the
	// current 5 ms/char delay (CX_CHARS_PER_SEC in clipboard.js).
	function typingSeconds(n) {
		return Math.max(1, Math.round((n * CX_TYPE_DELAY_MS) / 100) / 10);
	}
	// Pure: takes the length so the reactive statement below can pass
	// `$pasteText.length` as an EXPLICIT dependency — a bare call in the
	// template (`lengthNote()`) would not track the store read inside the
	// function body, and the warning would never update on typing or
	// file-open.
	function lengthNote(n) {
		if (n === 0) return "";
		if (n > PASTE_MAX_CHARS)
			return n.toLocaleString() + " chars — too long (max " + PASTE_MAX_CHARS + "), won't paste";
		if (n > LONG_WARN_CHARS)
			return n.toLocaleString() + " chars — ~" + typingSeconds(n) + "s to type";
		return "";
	}
	$: pasteLengthNote = lengthNote($pasteText.length);
</script>

<h1 class="text-lg font-bold">Clipboard</h1>
<p>Type, paste (Ctrl+V), or drop a file here, then click <b>Paste</b> — it is typed into the VM exactly as if you had typed it by hand. Only plain text you could actually type (ASCII) is accepted; anything else is refused with a reason.</p>
<textarea
	bind:value={$pasteText}
	rows="4"
	class="w-full bg-neutral-800 text-gray-100 text-sm font-mono rounded p-2"
	placeholder="Type, paste, or drop a file here, then click Paste"
	on:dragover={onDragOver}
	on:drop={onDrop}
/>
<div class="flex items-center gap-2 mt-1 text-xs">
	<button
		type="button"
		class="text-gray-300 hover:text-white underline underline-offset-2"
		on:click={openFileDialog}
	>Open file…</button>
	{#if pasteLengthNote}
		<span class="text-amber-400">{pasteLengthNote}</span>
	{:else}
		<span class="text-gray-500">or drag a file onto the box</span>
	{/if}
</div>
<input type="file" class="hidden" bind:this={fileInput} on:change={onFilePicked} on:cancel={onFileCancelled} />
<svelte:window on:pointerdown={onWindowPointerDown} />
<PanelButton
	buttonIcon="fas fa-arrow-right"
	clickHandler={pasteToVM}
	buttonTooltip="Send the box above into the VM"
	buttonText="Paste"
/>
{#if $pasteStatus}
	<p class="text-amber-400 text-sm">{$pasteStatus}</p>
{/if}
