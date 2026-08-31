import { describe, it, expect } from 'vitest';
import {
	pasteUntypableReason,
	encodePasteFrame,
	pasteAckTimeoutMs,
	consumePasteAcks,
	PASTE_MAX_CHARS,
	CX_TYPE_DELAY_MS,
	CX_CHARS_PER_SEC,
} from './clipboard.js';

// The page-side half of the CXCLIP/CXACK paste contract. The GUEST side of
// the same protocol is pinned by tests/unit/test_paste_typer.py — these tests
// mirror its expectations (typability rule, refusal diagnostic format, ack
// framing) so the two ends of the wire cannot drift apart.

describe('paste speed contract', () => {
	it('CX_CHARS_PER_SEC is the reciprocal of CX_TYPE_DELAY_MS', () => {
		expect(CX_CHARS_PER_SEC).toBe(Math.round(1000 / CX_TYPE_DELAY_MS));
		expect(CX_TYPE_DELAY_MS).toBeGreaterThan(0);
	});

	it('the panel estimate math matches the paste rate', () => {
		// typingSeconds(n) in PasteTab = round(n * CX_TYPE_DELAY_MS / 100) / 10
		const estimate = (n) => Math.round((n * CX_TYPE_DELAY_MS) / 100) / 10;
		expect(estimate(500)).toBe(2.5);
		expect(estimate(900)).toBe(4.5);
	});
});

describe('pasteUntypableReason', () => {
	it('accepts all printable ASCII plus newline/tab/backspace', () => {
		let printable = '';
		for (let c = 0x20; c <= 0x7e; c++) printable += String.fromCharCode(c);
		expect(pasteUntypableReason(printable + '\n\t\b')).toBeNull();
	});

	it('accepts an empty string', () => {
		expect(pasteUntypableReason('')).toBeNull();
	});

	it('refuses control characters with the exact U+%04X diagnostic', () => {
		expect(pasteUntypableReason('a\x01b')).toBe(
			'char U+0001 ("\\u0001") at index 1'
		);
	});

	it('refuses non-ASCII with the exact U+%04X diagnostic', () => {
		// é is U+00E9 — the same 4-digit padded format the guest awk prints.
		expect(pasteUntypableReason('café')).toBe(
			'char U+00E9 ("é") at index 3'
		);
		// Japanese — U+65E5 — must be refused, never typed wrong.
		expect(pasteUntypableReason('日本語')).toBe(
			'char U+65E5 ("日") at index 0'
		);
	});

	it('reports the FIRST offending character', () => {
		expect(pasteUntypableReason('ok\n\x07')).toBe(
			'char U+0007 ("\\u0007") at index 3'
		);
	});
});

describe('encodePasteFrame', () => {
	it('emits CXCLIP <len> <base64> with a trailing newline', () => {
		const frame = encodePasteFrame('hello');
		expect(frame).toBe('CXCLIP 5 ' + btoa('hello') + '\n');
	});

	it('round-trips multi-byte text (byte length, not char length)', () => {
		const text = 'héllo wörld';
		const frame = encodePasteFrame(text);
		const match = /^CXCLIP (\d+) ([\w+/=]+)\n$/.exec(frame);
		expect(match).toBeTruthy();
		expect(Number(match[1])).toBe(new TextEncoder().encode(text).length);
		// atob yields a binary (Latin-1) string; decode the bytes as UTF-8
		// to recover the original text.
		const bytes = Uint8Array.from(atob(match[2]), (c) => c.charCodeAt(0));
		expect(new TextDecoder().decode(bytes)).toBe(text);
	});

	it('handles frames larger than the 8192-char btoa chunk', () => {
		const text = 'x'.repeat(20000);
		const frame = encodePasteFrame(text);
		expect(frame.startsWith('CXCLIP 20000 ')).toBe(true);
		expect(atob(frame.slice('CXCLIP 20000 '.length, -1))).toBe(text);
	});
});

describe('pasteAckTimeoutMs', () => {
	it('scales with the pasted length', () => {
		expect(pasteAckTimeoutMs(0)).toBe(5000);
		expect(pasteAckTimeoutMs(10000)).toBe(5000 + 10000 * 20);
	});
});

describe('consumePasteAcks', () => {
	function collector() {
		const acks = [];
		const fails = [];
		return {
			acks,
			fails,
			handlers: {
				onAck: (len) => acks.push(len),
				onFail: () => fails.push(true),
			},
		};
	}

	it('releases one CXACK frame with its length', () => {
		const c = collector();
		const rest = consumePasteAcks('CXACK 42\n', c.handlers);
		expect(c.acks).toEqual([42]);
		expect(c.fails).toEqual([]);
		expect(rest).toBe('');
	});

	it('handles multiple frames in one chunk', () => {
		const c = collector();
		const rest = consumePasteAcks('CXACK 1\nCXACK 2\n', c.handlers);
		expect(c.acks).toEqual([1, 2]);
		expect(rest).toBe('');
	});

	it('reassembles lines split across chunks', () => {
		const c = collector();
		let rest = consumePasteAcks('CXAC', c.handlers);
		rest = consumePasteAcks(rest + 'K 7\n', c.handlers);
		expect(c.acks).toEqual([7]);
		expect(rest).toBe('');
	});

	it('a non-numeric CXACK length becomes 0', () => {
		const c = collector();
		consumePasteAcks('CXACK oops\n', c.handlers);
		expect(c.acks).toEqual([0]);
	});

	it('CXFAIL invokes the failure handler', () => {
		const c = collector();
		consumePasteAcks('CXFAIL untypable char U+00E9\n', c.handlers);
		expect(c.fails).toHaveLength(1);
		expect(c.acks).toEqual([]);
	});

	it('keeps only a 64-char tail of un-terminated fragments', () => {
		const c = collector();
		const rest = consumePasteAcks('noise' + 'X'.repeat(200), c.handlers);
		expect(rest.length).toBe(64);
		expect(c.acks).toEqual([]);
	});
});
