// Shared WebDAV Basic-auth header for the E2E scripts and specs.
//
// Playwright's APIRequestContext `auth` option does not send Basic auth on
// plain-HTTP requests (returns 401) — every poller must send the header
// explicitly. Keep the construction here so repro/spec/probes cannot drift
// apart (networking-bug.md §16.4).

export function basicAuthHeaders(user, pass) {
	return {
		Authorization:
			'Basic ' + Buffer.from(user + ':' + pass).toString('base64'),
	};
}
