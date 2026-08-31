import { describe, it, expect } from 'vitest';
import {
	parseResetDeadline,
	readBakedResetDeadline,
	secondsUntilDeadline,
	formatCountdown,
} from './resetCountdown.js';

// The page-side half of the periodic storage-reset countdown. The SERVER side
// of the contract is pinned by tests/unit/test_scripts.py (render-webvm-config
// --reset-deadline + the reset-cycle.sh deadline file) and test_entrypoint.py
// (the entrypoint bakes resetDeadline only when RESET_INTERVAL_HOURS is set) —
// these tests pin the client's reading/formatting so the two ends cannot drift.

describe('parseResetDeadline', () => {
	it('accepts a positive whole-number epoch', () => {
		expect(parseResetDeadline(1750000000)).toBe(1750000000);
	});

	it('rejects missing, non-number, non-finite, and non-positive values', () => {
		expect(parseResetDeadline(undefined)).toBeNull();
		expect(parseResetDeadline(null)).toBeNull();
		expect(parseResetDeadline('1750000000')).toBeNull();
		expect(parseResetDeadline(0)).toBeNull();
		expect(parseResetDeadline(-5)).toBeNull();
		expect(parseResetDeadline(Infinity)).toBeNull();
		expect(parseResetDeadline(NaN)).toBeNull();
		expect(parseResetDeadline(12.5)).toBeNull();
	});
});

describe('readBakedResetDeadline', () => {
	it('reads resetDeadline from the baked config object', () => {
		expect(readBakedResetDeadline({ resetDeadline: 1750000000 })).toBe(1750000000);
	});

	it('returns null when the config carries no resetDeadline', () => {
		expect(readBakedResetDeadline({})).toBeNull();
		expect(readBakedResetDeadline({ authKey: 'hskey-auth-x' })).toBeNull();
		expect(readBakedResetDeadline(null)).toBeNull();
		expect(readBakedResetDeadline(undefined)).toBeNull();
	});

	it('rejects a malformed deadline exactly like parseResetDeadline', () => {
		expect(readBakedResetDeadline({ resetDeadline: 'soon' })).toBeNull();
		expect(readBakedResetDeadline({ resetDeadline: 0 })).toBeNull();
	});
});

describe('secondsUntilDeadline', () => {
	it('is the whole-second difference (floor)', () => {
		expect(secondsUntilDeadline(1000.5, 1000)).toBe(0); // < 1s left
		expect(secondsUntilDeadline(1010, 1000.9)).toBe(9);
	});

	it('clamps to 0 once the deadline has passed', () => {
		expect(secondsUntilDeadline(1000, 1001)).toBe(0);
		expect(secondsUntilDeadline(1000, 5000)).toBe(0);
	});
});

describe('formatCountdown', () => {
	it('formats HH:MM:SS zero-padded', () => {
		expect(formatCountdown(0)).toBe('00:00:00');
		expect(formatCountdown(59)).toBe('00:00:59');
		expect(formatCountdown(60)).toBe('00:01:00');
		expect(formatCountdown(3661)).toBe('01:01:01');
		expect(formatCountdown(6 * 3600)).toBe('06:00:00');
	});

	it('clamps fractional and negative inputs', () => {
		expect(formatCountdown(1.9)).toBe('00:00:01');
		expect(formatCountdown(-10)).toBe('00:00:00');
	});
});
