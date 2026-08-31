import { describe, it, expect } from 'vitest';
import { CACHE_ID_PREFIX, sharedCacheId, ephemeralCacheId } from './cacheId.js';

// The overlay cacheId derivation: the E2E persistence contract matches the
// `blocks_alpine_` prefix, so these rules are pinned here.

describe('cacheId', () => {
	it('shared embeds the image-build fingerprint', () => {
		expect(CACHE_ID_PREFIX).toBe('blocks_alpine_');
		expect(sharedCacheId('a1b2c3d4e5f6')).toBe('blocks_alpine_a1b2c3d4e5f6');
	});

	it('ephemeral ids are unique per call and share the prefix', () => {
		const a = ephemeralCacheId();
		const b = ephemeralCacheId();
		expect(a).toMatch(/^blocks_alpine_/);
		expect(b).toMatch(/^blocks_alpine_/);
		expect(a).not.toBe(b);
	});
});
