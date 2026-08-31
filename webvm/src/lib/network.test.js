import { describe, it, expect } from 'vitest';
import { updateButtonData, NETWORK_STATES, networkData, applyControlSocketClose, validateLoginUrl } from './network.js';

// updateButtonData is the pure state->button-config mapping for the
// Networking sidebar panel (10 states). The state string literals the UI
// switches on live in NETWORK_STATES — this test also pins the canonical
// names so a rename cannot silently break NetworkingTab.svelte.

// validateLoginUrl is the login-URL gate applied to every URL the wasm
// client's loginUrlCb hands over (the login popup is navigated to it): only
// https/http may pass, anything else must throw.
describe('validateLoginUrl', () => {
	it('accepts https and http absolute URLs', () => {
		expect(validateLoginUrl('https://login.example.test/url')).toBe('https://login.example.test/url');
		expect(validateLoginUrl('http://login.example.test/url')).toBe('http://login.example.test/url');
	});

	it('rejects non-http(s) schemes', () => {
		expect(() => validateLoginUrl('javascript:alert(1)')).toThrow();
		expect(() => validateLoginUrl('file:///etc/passwd')).toThrow();
		expect(() => validateLoginUrl('data:text/html,x')).toThrow();
	});

	it('rejects relative and malformed URLs', () => {
		expect(() => validateLoginUrl('/ts2021')).toThrow();
		expect(() => validateLoginUrl('')).toThrow();
		expect(() => validateLoginUrl('not a url')).toThrow();
	});
});

describe('updateButtonData', () => {
	const hc = () => {};
	const hcc = () => {};

	it('DISCONNECTED offers Connect with the handler', () => {
		const b = updateButtonData(NETWORK_STATES.DISCONNECTED, hc);
		expect(b.buttonText).toBe('Connect to Tailscale');
		expect(b.isClickable).toBe(true);
		expect(b.clickHandler).toBe(hc);
		expect(b.clickUrl).toBeNull();
	});

	it('DOWNLOADING is inert', () => {
		const b = updateButtonData(NETWORK_STATES.DOWNLOADING, hc);
		expect(b.isClickable).toBe(false);
		expect(b.clickHandler).toBeNull();
	});

	it('LOGINSTARTING is inert', () => {
		const b = updateButtonData(NETWORK_STATES.LOGINSTARTING, hc);
		expect(b.isClickable).toBe(false);
	});

	it('LOGINREADY carries the login URL as a clickUrl', () => {
		networkData.loginUrl = 'https://login.example.test/url';
		const b = updateButtonData(NETWORK_STATES.LOGINREADY, hc);
		expect(b.buttonText).toBe('Login to Tailscale');
		expect(b.isClickable).toBe(true);
		expect(b.clickUrl).toBe('https://login.example.test/url');
		expect(b.clickHandler).toBeNull();
	});

	it('LOGINFAILED is inert', () => {
		const b = updateButtonData(NETWORK_STATES.LOGINFAILED, hc);
		expect(b.buttonText).toBe('Invalid login URL');
		expect(b.isClickable).toBe(false);
	});

	it('UNREACHABLE is the retry affordance (clickable, handler, no URL)', () => {
		const b = updateButtonData(NETWORK_STATES.UNREACHABLE, hcc);
		expect(b.isClickable).toBe(true);
		expect(b.clickHandler).toBe(hcc);
		expect(b.clickUrl).toBeNull();
	});

	it('CONNECTED shows the IP, links the dashboard, copies on right-click', () => {
		networkData.currentIp = '100.64.0.1';
		networkData.loginUrl = null;
		const b = updateButtonData(NETWORK_STATES.CONNECTED, hc);
		expect(b.buttonText).toBe('IP: 100.64.0.1');
		expect(b.isClickable).toBe(true);
		expect(b.clickUrl).toBe(networkData.dashboardUrl);
		expect(b.rightClickHandler).toBeTruthy();
	});

	it('IPCOPIED is transient feedback', () => {
		const b = updateButtonData(NETWORK_STATES.IPCOPIED, hc);
		expect(b.buttonText).toBe('Copied!');
		expect(b.isClickable).toBe(false);
	});

	it('unknown states fall back to a diagnostic text', () => {
		const b = updateButtonData('BOGUS', hc);
		expect(b.buttonText).toBe('Text for state: BOGUS');
		expect(b.isClickable).toBe(false);
	});

	it('NETWORK_STATES are the exact literals the UI compares against', () => {
		expect(NETWORK_STATES.DISCONNECTED).toBe('DISCONNECTED');
		expect(NETWORK_STATES.DOWNLOADING).toBe('DOWNLOADING');
		expect(NETWORK_STATES.LOGINSTARTING).toBe('LOGINSTARTING');
		expect(NETWORK_STATES.LOGINREADY).toBe('LOGINREADY');
		expect(NETWORK_STATES.LOGINFAILED).toBe('LOGINFAILED');
		expect(NETWORK_STATES.UNREACHABLE).toBe('UNREACHABLE');
		expect(NETWORK_STATES.CONNECTED).toBe('CONNECTED');
		expect(NETWORK_STATES.IPCOPIED).toBe('IPCOPIED');
		expect(Object.isFrozen(NETWORK_STATES)).toBe(true);
	});
});

// applyControlSocketClose is the pure close-event decision behind the
// control-plane rejection watchdog (a stale/rejected authKey makes the wasm
// client loop wss://…/ts2021 open→immediate-close forever; after
// HANDSHAKE_FAILURE_LIMIT consecutive rejected handshakes the session trips
// to UNREACHABLE and control sockets are suppressed).
describe('applyControlSocketClose', () => {
	it('a session that reached Running resets the failure streak', () => {
		const verdict = applyControlSocketClose(true, 4);
		expect(verdict.handshakeFailures).toBe(0);
		expect(verdict.shouldTrip).toBe(false);
	});

	it('increments the failure count on each rejected handshake', () => {
		const verdict = applyControlSocketClose(false, 2);
		expect(verdict.handshakeFailures).toBe(3);
		expect(verdict.shouldTrip).toBe(false);
	});

	it('trips exactly at the default limit of 5', () => {
		const verdict = applyControlSocketClose(false, 4);
		expect(verdict.shouldTrip).toBe(true);
		expect(verdict.handshakeFailures).toBe(5);
	});

	it('a custom limit is honored', () => {
		expect(applyControlSocketClose(false, 2, 3).shouldTrip).toBe(true);
		expect(applyControlSocketClose(false, 1, 3).shouldTrip).toBe(false);
	});

	it('the streak accumulates across calls like the live listener', () => {
		let failures = 0;
		let tripped = false;
		for (let i = 0; i < 6 && !tripped; i++) {
			const v = applyControlSocketClose(false, failures);
			failures = v.handshakeFailures;
			tripped = v.shouldTrip;
			if (tripped) failures = 0; // the live listener resets on trip
		}
		expect(tripped).toBe(true);
		expect(failures).toBe(0);
	});
});
