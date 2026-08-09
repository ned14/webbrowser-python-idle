// No external blog-post fetches (LAN-only: zero external requests at build
// and at runtime). The posts feed is empty by design.
export async function load()
{
	return { posts: [] };
}
