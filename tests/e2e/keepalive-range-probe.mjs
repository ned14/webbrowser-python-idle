#!/usr/bin/env node
// Keep-alive range-read comparison — approximates how the VM's XHR byte
// device actually reads (connection reuse), unlike one-shot curl.
// Measures sequential 128 KiB range reads on ONE reused connection, plus a
// 6-way concurrent burst, for (a) via Cloudflare and (b) direct to origin.
import https from 'node:https';

const RANGE = 'bytes=60000000-60131071';
const CF_URL = 'https://webvm.nedprod.com/custom-disk-images/webvm-custom-disk.ext2?v=df053cffbb91';
const ORIGIN_IP = '82.47.22.78';

function readOnce(agent, host) {
	return new Promise((resolve, reject) => {
		const t0 = performance.now();
		const headers = { Range: RANGE };
		if (host === ORIGIN_IP) headers.Host = 'webvm.nedprod.com';
		const req = https.request(CF_URL, {
			agent,
			method: 'GET',
			headers,
			rejectUnauthorized: false,
		}, (res) => {
			res.resume();
			res.on('end', () => resolve({ ms: performance.now() - t0, status: res.statusCode, reused: req.reusedSocket }));
		});
		req.on('error', reject);
		req.end();
	});
}

function makeAgent(toOrigin) {
	return new https.Agent({
		keepAlive: true,
		maxSockets: toOrigin ? 1 : 6,
		rejectUnauthorized: false,
		lookup: toOrigin
			? (hostname, opts, cb) => {
				if (opts && opts.all) cb(null, [{ address: ORIGIN_IP, family: 4 }]);
				else cb(null, ORIGIN_IP, 4);
			}
			: undefined,
	});
}

async function measure(label, toOrigin, n) {
	const agent = makeAgent(toOrigin);
	const lat = [];
	let reused = 0;
	for (let i = 0; i < n; i++) {
		const r = await readOnce(agent, toOrigin ? ORIGIN_IP : 'cf');
		if (i > 0) lat.push(r.ms); // drop the connection-establishing first read
		if (r.reused) reused++;
	}
	agent.destroy();
	lat.sort((a, b) => a - b);
	const mean = lat.reduce((a, b) => a + b, 0) / lat.length;
	console.log(`${label}: n=${lat.length} median=${lat[Math.floor(lat.length/2)].toFixed(0)}ms mean=${mean.toFixed(0)}ms min=${lat[0].toFixed(0)}ms max=${lat[lat.length-1].toFixed(0)}ms (${reused}/${n-1} reads reused the socket)`);
}

console.log('Sequential range reads on ONE reused connection (warm reads):');
await measure('via Cloudflare ', false, 16);
await measure('direct to origin', true, 16);
await measure('via Cloudflare (2nd) ', false, 16);
await measure('direct to origin (2nd)', true, 16);
