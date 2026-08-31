import { writable } from 'svelte/store';

// The paste contract, shared by WebVM.svelte (the sender) and PasteTab.svelte
// (the panel): the page-side hard cap on paste size, and the guest's typing
// delay per character (paste-typer.sh DELAY_US). Both consumers import these
// so the two length/time models cannot drift apart.
export const PASTE_MAX_CHARS = 10000;
// The guest's typing delay per char (paste-typer.sh DELAY_US — 5 ms since
// 2026-08-29, halved from 10 ms: ~200 chars/s instead of ~100). Both
// consumers import these so the two length/time models cannot drift apart.
// The ack timeout below is a conservative BOUND either way (it does not
// need to track the exact rate). CX_CHARS_PER_SEC is the derived rate the
// panel's typing-time estimate and docs reference (tests/unit/test_scripts.py
// pins DELAY_US === CX_TYPE_DELAY_MS * 1000 so the guest and page cannot
// drift apart).
export const CX_TYPE_DELAY_MS = 5;
export const CX_CHARS_PER_SEC = Math.round(1000 / CX_TYPE_DELAY_MS);

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

// --------------------------------------------------------------------------
// The CXCLIP/CXACK wire contract — the SAME protocol the guest
// paste-typer.sh implements (tests/unit/test_paste_typer.py pins the guest
// side). The page-side halves live here so they can be unit-tested against
// the same spec: the typability gate, the frame encoding, the ack timeout
// and the ack-line scanner.
// --------------------------------------------------------------------------

// The typability rule mirrors the guest typer's awk translate(): only
// printable ASCII plus \n \t \b can be typed out as keys. Returns null when
// the text can be typed, otherwise the exact diagnostic ("char U+%04X …" —
// the same 4-digit padded format the guest prints, so the refusal text is
// identical page-side and guest-side).
export function pasteUntypableReason(text)
{
	for(var i = 0; i < text.length; i++)
	{
		var code = text.codePointAt(i);
		if(code >= 0x20 && code <= 0x7E)
			continue;
		var ch = text[i];
		if(ch === "\n" || ch === "\t" || ch === "\b")
			continue;
		return "char U+" + ("0000" + code.toString(16).toUpperCase()).slice(-4) +
			" (" + JSON.stringify(ch) + ") at index " + i;
	}
	return null;
}

// Encode a paste payload as one `CXCLIP <len> <base64>\n` console frame.
// Chunked so a large frame cannot blow the call stack via
// Function.prototype.apply argument limits.
export function encodePasteFrame(text)
{
	var bytes = new TextEncoder().encode(text);
	var bin = "";
	for(var i = 0; i < bytes.length; i += 8192)
		bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
	return "CXCLIP " + bytes.length + " " + btoa(bin) + "\n";
}

// The guest types at CX_TYPE_DELAY_MS per char; the page-side ack timeout
// scales with the text length (a 10k-char paste takes a while to type).
export function pasteAckTimeoutMs(len)
{
	return 5000 + len * 20;
}

// Consume complete CXACK/CXFAIL lines out of the reassembled console-stream
// buffer (lines may span chunks, so the caller keeps the returned remainder
// and prepends it to the next chunk). Returns the leftover buffer, trimmed
// to 64 chars (only CX-prefixed fragments are held back).
export function consumePasteAcks(buffer, handlers)
{
	var rest = buffer;
	while(true)
	{
		var nl = rest.indexOf("\n");
		if(nl < 0)
			break;
		var line = rest.slice(0, nl);
		rest = rest.slice(nl + 1);
		if(line.indexOf("CXACK ") === 0)
		{
			var n = parseInt(line.slice(6), 10);
			handlers.onAck(isNaN(n) ? 0 : n);
		}
		else if(line.indexOf("CXFAIL") === 0)
			handlers.onFail();
	}
	if(rest.length > 64)
		rest = rest.slice(-64);
	return rest;
}
