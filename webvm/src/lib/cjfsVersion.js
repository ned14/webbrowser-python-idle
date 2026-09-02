// cjFS versioning — the guest-persistent CheerpX folder filesystem.
//
// The CheerpX runtime keeps a persistent folder FS in IndexedDB under the
// fixed name `cjFS_<mountPoint>` (upstream cheerpOS.js: indexedDB.open with
// "cjFS_"+mp.mountPoint — never versioned against the guest image; upstream
// carries a literal "TODO: Verify IndexDB version" there). Only the
// CheerpJIndexedDBFolder "/files/" mount exists in the vendored runtime, so
// the database is "cjFS_/files/".
//
// The block overlay ON TOP of the disk image is already versioned per image
// build (`blocks_alpine_<image-build>`, see $lib/cacheId.js): a rebuilt image
// starts a fresh overlay instead of applying stale deltas to a new base. The
// cjFS folder DB has no such versioning, so records written by OLDER
// runtimes/images persist across deployments and are read on every boot. A
// runtime-glue change between releases (observed: the CheerpX 1.3.7 -> 1.3.8
// bump) turns those stale records into garbage fed into the wasm message
// dispatch, killing the boot with "Uncaught RuntimeError: table index is out
// of bounds" inside cheerpOSOpenMain -> idbMakeFileData (2026-09-01, live
// Pages site; a fresh browser profile boots the same deployment cleanly).
//
// Fix: whenever the image-build fingerprint differs from the one the last
// boot used (including the very first load under this marker — the one-time
// migration that also repairs browsers already holding poisoned records), the
// app's OWN cjFS IndexedDB family is deleted BEFORE the VM starts. Same
// policy as the overlay: a rebuilt image starts a fresh guest-persistent FS.
//
// Scope: only this app's databases are ever deleted — the vendored runtime's
// CheerpJIndexedDBFolder mount ("cjFS_/files/", upstream cheerpOS.js) and the
// runtime-prefixed overlay family ("cjFS_/blocks_alpine_<build>/", pinned by
// tests/e2e/tests/persistence.spec.js). Everything else that shares the
// origin (a GitHub account's OTHER project sites all live under the same
// https://<user>.github.io origin, and IndexedDB is origin-scoped, not
// path-scoped) is left untouched.
import { CACHE_ID_PREFIX } from './cacheId.js';

// Where the last-used fingerprint is remembered. Lives under the same
// localStorage family as the session guard (persistent per origin; NOT in
// IndexedDB, so it stays readable even when the target databases are not).
export const CACHE_ID_MARKER_PREFIX = `${CACHE_ID_PREFIX}cjfs-image-build`;

// Invariant (pinned by cjfsVersion.test.js): the marker embeds the overlay
// prefix so the E2E persistence contract and a possible future store audit
// can enumerate both naming families from one string constant.
export function cjfsMarkerKey() {
	return CACHE_ID_MARKER_PREFIX;
}

// Pure decision — extracted for unit testing.
// reset = the stored marker is absent (first run of the versioned frontend
//         OR a browser that predates this migration: repair required) or
//         differs from the image currently being booted.
export function shouldResetCjfs(marker, imageBuild) {
	return marker !== imageBuild;
}

// Is <name> one of THIS app's databases? (see the scope note in the header)
function isOwnCjfsDb(name) {
	return name === 'cjFS_/files/' || name.startsWith('cjFS_/blocks_alpine_');
}

// The per-load overlay sweep (2026-09-02, ephemeral-overlay model): deletes
// ONLY the app's own overlay family ('cjFS_/blocks_alpine_*' — the cacheId
// derivation in $lib/cacheId.js keeps every per-session id inside the
// 'blocks_alpine_' prefix). Unlike deleteCjfsDatabases() this deliberately
// does NOT touch 'cjFS_/files/': that name is the CheerpX runtime's generic
// folder-FS database for ANY app mounting a CheerpJIndexedDBFolder at
// /files/ on the same origin, and IndexedDB is origin-scoped, not
// path-scoped — on a shared origin (e.g. every GitHub Pages project of one
// account) a blanket per-load deletion would destroy a co-tenant CheerpX
// app's persistent data. This app never creates 'cjFS_/files/' (its folder
// mounts are per-cacheId), so the per-load sweep never needs to.
export async function deleteOverlayDatabases() {
	const names = [];
	try {
		if (typeof indexedDB !== 'undefined' && indexedDB.databases) {
			const dbs = await indexedDB.databases();
			names.push(...dbs.map((d) => d.name).filter((n) => n && n.startsWith('cjFS_/blocks_alpine_')));
		}
	} catch (e) {
		// enumeration unavailable — nothing to sweep by name
	}
	let allSucceeded = true;
	for (const name of names) {
		const ok = await new Promise((resolve) => {
			const req = indexedDB.deleteDatabase(name);
			req.onsuccess = () => resolve(true);
			req.onerror = () => resolve(false);
			// Another live tab's session holds the DB open; the delete is
			// blocked and simply skipped (that tab's next load sweeps it).
			req.onblocked = () => resolve(false);
		});
		if (!ok) allSucceeded = false;
	}
	return allSucceeded;
}

// Delete this app's cjFS_* databases. Best-effort; never throws. Returns
// true ONLY when every requested deletion actually completed — a blocked
// delete (another tab's live VM holds the database open) or an errored
// delete returns false, signalling the caller to NOT record the migration
// marker so the wipe retries on a later boot. Uses indexedDB.databases()
// when available, falling back to the known "/files/" mount name when it is
// not (every browser with the overlay family supports databases()).
export async function deleteCjfsDatabases() {
	const names = [];
	try {
		if (typeof indexedDB !== 'undefined' && indexedDB.databases) {
			const dbs = await indexedDB.databases();
			names.push(...dbs.map((d) => d.name).filter((n) => n && isOwnCjfsDb(n)));
		}
	} catch (e) {
		// indexedDB.databases() unavailable or blocked — fall back to the
		// known-name list below.
	}
	// Fallback (enumeration unavailable or found nothing): the vendored
	// runtime's CheerpJIndexedDBFolder mount.
	if (names.length === 0) names.push('cjFS_/files/');
	let allSucceeded = true;
	for (const name of names) {
		const ok = await new Promise((resolve) => {
			const req = indexedDB.deleteDatabase(name);
			req.onsuccess = () => resolve(true);
			req.onerror = () => resolve(false);
			// Another tab's session holds the DB open. The request stays
			// armed and will fire when that session closes; leaving the
			// marker unset makes the NEXT boot retry (and re-arm) instead of
			// silently accepting a never-performed wipe.
			req.onblocked = () => resolve(false);
		});
		if (!ok) allSucceeded = false;
	}
	return allSucceeded;
}

// Runs the migration once per image build. Safe to call before the session
// lock is even acquired: at that point NO VM is mounted in this tab, so the
// databases are not in use here; if another tab's live session blocks the
// deletion, the marker is NOT recorded, so the next boot retries.
// Returns true when the reset was actually completed.
export async function resetCjfsIfImageChanged(imageBuild) {
	let marker = null;
	try {
		marker = localStorage.getItem(cjfsMarkerKey());
	} catch (e) {
		// storage blocked: nothing we can do — a visitor with blocked storage
		// gets a fresh session anyway.
		return false;
	}
	if (!shouldResetCjfs(marker, imageBuild)) return false;
	const completed = await deleteCjfsDatabases();
	// Record the marker ONLY once every deletion actually happened — a
	// blocked/errored wipe must be retried on the next boot, never skipped.
	if (!completed) return false;
	try {
		localStorage.setItem(cjfsMarkerKey(), imageBuild);
	} catch (e) {
		// best-effort; next boot will re-run the (now harmless) reset
	}
	return true;
}