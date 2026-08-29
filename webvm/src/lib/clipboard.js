import { writable } from 'svelte/store';

// One-line status for the Clipboard sidebar panel: paste refusals (text
// that cannot be typed as keys), oversize warnings, and the fire-and-forget
// "Pasted into the VM" confirmation. Set by WebVM.svelte, shown by
// PasteTab.svelte.
export const pasteStatus = writable("");

// The panel's draft text lives in a STORE so it survives the panel closing
// and reopening (PasteTab unmounts when the panel hides): content loaded
// from a file or typed in is never reset by the panel hiding.
export const pasteText = writable("");

// True while the native file picker may be open. The sidebar panel
// auto-closes on hover-away (mouseleave -> 400 ms timer), and moving the
// mouse over to the file dialog fires that mouseleave — which would close
// the panel mid-flow and drop the chosen file. SideBar.svelte suppresses
// the auto-close while this is set. PasteTab clears it ONLY when the
// picker is known closed (change/cancel on the input, or the first page
// pointerdown — native pickers are modal); never on a timer or window
// focus (Chrome fires focus while the dialog is open).
export const filePickerActive = writable(false);
