#!/usr/bin/env node
// Range-read latency/throughput probe: N sequential 128 KiB range GETs at
// boot-like offsets, reporting per-request latency + aggregate throughput.
const N = Number(process.argv[2] || 20);
const OFF = Number(process.argv[3] || 60000000); // ~60 MiB offset (late-boot region)
const URL = process.argv[4];

async function one() {
	const t0 = performance.now();
	const res = await fetch(URL, { headers: { Range: `bytes=${OFF}-${OFF + 131071}` } });
	if (res.status !== 206) throw new Error('status ' + res.status);
	await res.arrayBuffer();
	return performance.now() - t0;
}

(async () => {
	const lat = [];
	for (let i = 0; i < N; i++) lat.push(await one());
	lat.sort((a, b) => a - b);
	const mean = lat.reduce((a, b) => a + b, 0) / lat.length;
	const totalBytes = N * 131072;
	const secs = lat.reduce((a, b) => a + b, 0) / 1000;
	console.log(`url=${URL}`);
	console.log(`n=${N} mean=${mean.toFixed(1)}ms median=${lat[Math.floor(N/2)].toFixed(1)}ms min=${lat[0].toFixed(1)}ms max=${lat[N-1].toFixed(1)}ms`);
	console.log(`sequential throughput: ${(totalBytes / secs / 1e6).toFixed(1)} MB/s (serial); per-read ~${mean.toFixed(0)}ms`);
})();
