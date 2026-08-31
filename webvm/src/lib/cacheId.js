// cacheId derivation — single source of truth for the IndexedDB overlay key.
//
//   browser/samba/webdav -> blocks_alpine_<image-build> (shared overlay,
//   versioned to the guest image: a rebuilt image starts a fresh overlay
//   instead of applying stale deltas to a new base; a content-identical
//   rebuild keeps the same fingerprint and therefore the same overlay).
//   none                 -> random per-session id (fresh overlay every load).
//
// The ephemeral id deliberately shares the prefix with the shared id (only
// the suffix differs) so both overlay kinds live under one IndexedDB naming
// family; the E2E persistence contract matches the prefix.
export const CACHE_ID_PREFIX = "blocks_alpine_";

export function sharedCacheId(imageBuild) {
	return CACHE_ID_PREFIX + imageBuild;
}

export function ephemeralCacheId() {
	return CACHE_ID_PREFIX + Math.random().toString(36).slice(2) + Date.now().toString(36);
}
